import React, { useState, useEffect, useRef } from 'react';

export default function RunReview({ runId, onBack }) {
  const [run, setRun] = useState(null);
  const [questions, setQuestions] = useState([]);
  const [sandboxEvents, setSandboxEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [answering, setAnswering] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [showCode, setShowCode] = useState(false);
  const [hoveredCitation, setHoveredCitation] = useState(null);
  const pollTimerRef = useRef(null);

  const fetchRunDetails = async () => {
    try {
      const res = await fetch(`/api/runs/${runId}`);
      if (!res.ok) return;
      const data = await res.json();
      if (data.run) {
        setRun(data.run);

        const rawQs = data.questions || [];
        rawQs.sort((a, b) => {
          const statusOrder = { gap: 0, unsupported: 0, partial: 1, answered: 2, supported: 2 };
          const stA = statusOrder[a.answer?.evidence_status] ?? 0;
          const stB = statusOrder[b.answer?.evidence_status] ?? 0;
          if (stA !== stB) return stA - stB;
          const confA = a.answer?.confidence ?? 0;
          const confB = b.answer?.confidence ?? 0;
          return confA - confB;
        });

        setQuestions(rawQs);
        setSandboxEvents(data.sandbox_events || []);

        const currentStatus = data.run.status;
        if (currentStatus === 'answering') {
          setAnswering(true);
        } else {
          setAnswering(false);
        }

        if (currentStatus === 'exporting') {
          setExporting(true);
        } else if (currentStatus === 'exported') {
          setExporting(false);
        }

        if (['parsed', 'review_ready', 'failed', 'error'].includes(currentStatus)) {
          if (pollTimerRef.current) {
            clearInterval(pollTimerRef.current);
            pollTimerRef.current = null;
          }
        }
      }
    } catch (e) {
      console.error('Failed to fetch run details:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!runId) return;
    fetchRunDetails();
    pollTimerRef.current = setInterval(fetchRunDetails, 1500);

    return () => {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, [runId]);

  const handleAnswerAll = async () => {
    setAnswering(true);
    try {
      await fetch(`/api/runs/${runId}/answer-all`, { method: 'POST' });
      fetchRunDetails();
      if (!pollTimerRef.current) {
        pollTimerRef.current = setInterval(fetchRunDetails, 1500);
      }
    } catch (e) {
      console.error('Answer-all error:', e);
      setAnswering(false);
    }
  };

  const handleInlineAnswerEdit = (qId, newAnswer) => {
    setQuestions((prev) =>
      prev.map((q) => {
        if (q.id === qId) {
          return {
            ...q,
            answer: {
              ...(q.answer || {}),
              reviewed_answer: newAnswer,
              reviewer_edited: true,
            },
          };
        }
        return q;
      })
    );
  };

  const handleExport = async () => {
    setExporting(true);
    try {
      const res = await fetch(`/api/runs/${runId}/export`, { method: 'POST' });
      if (res.ok) {
        if (!pollTimerRef.current) {
          pollTimerRef.current = setInterval(fetchRunDetails, 1500);
        }
        const checkExportReady = setInterval(async () => {
          const detailRes = await fetch(`/api/runs/${runId}`);
          if (detailRes.ok) {
            const detailData = await detailRes.json();
            if (detailData.run?.status === 'exported') {
              clearInterval(checkExportReady);
              setExporting(false);
              window.location.href = `/api/runs/${runId}/export-file`;
            } else if (detailData.run?.status === 'failed') {
              clearInterval(checkExportReady);
              setExporting(false);
              alert('Export failed in Daytona Sandbox');
            }
          }
        }, 1000);
      } else {
        alert('Export request failed');
        setExporting(false);
      }
    } catch (e) {
      alert('Export error: ' + e.message);
      setExporting(false);
    }
  };

  if (loading) {
    return <div className="card"><p>Loading run details...</p></div>;
  }

  if (!run) {
    return <div className="card"><p>Run not found.</p></div>;
  }

  const totalQuestions = questions.length;
  const answeredCount = questions.filter(
    (q) => q.answer && (q.answer.evidence_status === 'answered' || q.answer.evidence_status === 'supported')
  ).length;
  const gapCount = questions.filter(
    (q) => !q.answer || q.answer.evidence_status === 'gap' || q.answer.evidence_status === 'unsupported'
  ).length;

  const elapsed = (run.elapsed_seconds !== undefined && run.elapsed_seconds !== null)
    ? Number(run.elapsed_seconds).toFixed(1)
    : '0.0';

  const gapGroups = {};
  questions.forEach((q) => {
    const st = q.answer?.evidence_status;
    if (st === 'gap' || st === 'partial' || st === 'unsupported') {
      const action = q.answer?.closes_gap_with || q.answer?.unsupported_reason || 'Upload missing policy document to Evidence Library';
      if (!gapGroups[action]) gapGroups[action] = [];
      gapGroups[action].push(q);
    }
  });

  const getStatusBannerText = () => {
    switch (run.status) {
      case 'queued':
        return { text: 'Status: Queued for Daytona Sandbox...', class: 'queued' };
      case 'creating_sandbox':
        return { text: 'Status: Provisioning Isolated Daytona Container Sandbox...', class: 'creating_sandbox' };
      case 'parsing':
        return { text: 'Status: Executing Model-Authored Parser in Daytona Sandbox...', class: 'parsing' };
      case 'parsed':
      case 'review_ready':
        return { text: 'Status: Questionnaire Parsed & Review Ready', class: 'answered' };
      case 'answering':
        return { text: 'Status: Drafting RAG Evidence Answers in Parallel...', class: 'answering' };
      case 'exporting':
        return { text: 'Status: Writing Filled Questionnaire File in Daytona Sandbox...', class: 'exporting' };
      case 'exported':
        return { text: 'Status: Export File Ready for Download', class: 'exported' };
      case 'failed':
      case 'error':
        return { text: 'Status: Execution Failed', class: 'failed' };
      default:
        return { text: `Status: ${run.status}`, class: 'partial' };
    }
  };

  const statusBanner = getStatusBannerText();

  return (
    <div className="run-review-page">
      {/* Page Title & Controls */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '24px' }}>
        <div>
          <button className="btn btn-secondary" onClick={onBack} style={{ marginBottom: '12px' }}>
            ← Back to Library
          </button>
          <h2 className="page-title" style={{ margin: 0 }}>{run.name}</h2>
        </div>
        <div style={{ display: 'flex', gap: '8px' }}>
          <button className="btn btn-secondary" onClick={handleAnswerAll} disabled={answering || run.status === 'queued' || run.status === 'creating_sandbox' || run.status === 'parsing'}>
            {answering ? 'Answering...' : 'Batch Answer All (RAG)'}
          </button>
          <button className="btn" onClick={handleExport} disabled={exporting || totalQuestions === 0}>
            {exporting ? 'Writing File...' : 'Export Original File (Daytona)'}
          </button>
        </div>
      </div>

      {/* Degraded (mock sandbox) Banner */}
      {run.degraded && (
        <div
          className="card-surface"
          style={{
            marginBottom: '16px',
            padding: '12px 14px',
            fontSize: '14px',
            color: 'var(--gap-fg)',
            borderLeft: '2px solid var(--gap-fg)',
          }}
        >
          Sandbox unavailable — this run did not execute in an isolated environment.
        </div>
      )}

      {/* Status Banner */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
        <span className={`status-pill ${statusBanner.class}`} style={{ fontSize: '13px', padding: '4px 12px' }}>
          {statusBanner.text}
        </span>
        {['queued', 'creating_sandbox', 'parsing', 'answering', 'exporting'].includes(run.status) && (
          <span className="mono-text" style={{ color: 'var(--ink-3)', fontVariantNumeric: 'tabular-nums' }}>
            Live Polling (1.5s)...
          </span>
        )}
      </div>

      {/* Error Message Render */}
      {(run.status === 'failed' || run.status === 'error') && (
        <div className="gap-register-panel" style={{ marginBottom: '24px' }}>
          <strong style={{ color: 'var(--gap-fg)' }}>Daytona Sandbox Exception:</strong>
          <p className="mono-text" style={{ color: 'var(--ink-2)', marginTop: '4px' }}>
            {run.error || 'An unexpected exception occurred during background sandbox processing.'}
          </p>
        </div>
      )}

      {/* Stat Tiles in Single Row */}
      <div className="stats-grid-single-row">
        <div className="stat-tile">
          <div className="stat-label">Total Questions</div>
          <div className="stat-value">{totalQuestions}</div>
        </div>
        <div className="stat-tile">
          <div className="stat-label">Evidence Backed</div>
          <div className="stat-value" style={{ color: 'var(--ok-fg)' }}>{answeredCount}</div>
        </div>
        <div className="stat-tile">
          <div className="stat-label">Admitted Gaps</div>
          <div className="stat-value" style={{ color: 'var(--gap-fg)' }}>{gapCount}</div>
        </div>
        <div className="stat-tile">
          <div className="stat-label">Elapsed Sandbox</div>
          <div className="stat-value">{elapsed}s</div>
        </div>
      </div>

      {/* Question & Evidence Review Matrix */}
      <div className="card">
        <div className="card-title">
          <span className="section-heading" style={{ margin: 0 }}>Question & Evidence Review Matrix</span>
        </div>
        <div className="table-responsive">
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ width: '90px' }}>Ref</th>
                <th style={{ width: '320px' }}>Question</th>
                <th>Answer (Editable)</th>
                <th style={{ width: '190px' }}>Evidence</th>
                <th style={{ width: '110px' }}>Confidence</th>
                <th style={{ width: '100px' }}>Status</th>
              </tr>
            </thead>
            <tbody>
              {questions.length === 0 ? (
                <tr>
                  <td colSpan="6" style={{ textAlign: 'center', padding: '32px', color: 'var(--ink-3)' }}>
                    {['queued', 'creating_sandbox', 'parsing'].includes(run.status)
                      ? 'Sandbox is creating container and extracting questions...'
                      : 'No questions extracted yet.'}
                  </td>
                </tr>
              ) : (
                questions.map((q) => {
                  const ans = q.answer || {};
                  const status = ans.evidence_status || 'gap';
                  const rawConf = ans.confidence ?? 0;
                  const percentConf = Math.round(rawConf * 100);
                  const answerText = ans.reviewed_answer || ans.draft_answer || '';
                  const barClass = status === 'answered' || status === 'supported' ? 'ok' : status === 'partial' ? 'warn' : 'gap';

                  return (
                    <tr key={q.id}>
                      <td className="mono-text" style={{ fontWeight: '600', color: 'var(--ink)' }}>{q.question_ref}</td>
                      <td style={{ color: 'var(--ink)', lineHeight: '1.5' }}>{q.question_text}</td>
                      <td>
                        <textarea
                          className="btn btn-secondary"
                          style={{ width: '100%', minHeight: '60px', textAlign: 'left', cursor: 'text', fontFamily: 'inherit', fontSize: '14px', padding: '8px 12px', lineHeight: '1.5' }}
                          value={answerText}
                          onChange={(e) => handleInlineAnswerEdit(q.id, e.target.value)}
                          placeholder={status === 'gap' ? 'No supporting evidence found. Admitted gap.' : ''}
                        />
                      </td>
                      <td style={{ position: 'relative' }}>
                        {ans.citation_document ? (
                          <span
                            className="doc-chip"
                            onMouseEnter={() => setHoveredCitation(ans)}
                            onMouseLeave={() => setHoveredCitation(null)}
                          >
                            {ans.citation_document} {ans.citation_clause ? `§${ans.citation_clause}` : ''}
                            {hoveredCitation === ans && (
                              <div className="popover">
                                <strong className="doc-chip">{ans.citation_document} {ans.citation_clause ? `§${ans.citation_clause}` : ''}</strong>
                                <p style={{ marginTop: '6px', color: 'var(--ink-2)' }}>
                                  "{ans.quote || 'Exact clause quote verified in policy evidence.'}"
                                </p>
                              </div>
                            )}
                          </span>
                        ) : (
                          <span className="mono-text" style={{ color: 'var(--ink-3)' }}>—</span>
                        )}
                      </td>
                      <td>
                        {status === 'gap' ? (
                          // The stored number is the model's confidence that this IS a gap.
                          // Rendering it beside a red gap pill reads as a confident answer.
                          <span className="mono-text" style={{ color: 'var(--ink-3)' }}>—</span>
                        ) : (
                          <div className="confidence-indicator">
                            <div className="confidence-bar-track">
                              <div className={`confidence-bar-fill ${barClass}`} style={{ width: `${percentConf}%` }} />
                            </div>
                            <span className="confidence-value">{percentConf}%</span>
                          </div>
                        )}
                      </td>
                      <td>
                        <span className={`status-pill ${status}`}>{status}</span>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Gap Register Panel: "What you cannot yet claim" */}
      <div className="gap-register-panel">
        <div className="section-heading" style={{ color: 'var(--gap-fg)', marginBottom: '8px' }}>
          What you cannot yet claim
        </div>
        <p style={{ color: 'var(--ink-2)', fontSize: '14px', marginBottom: '16px' }}>
          These compliance claims cannot be made without risking procurement auditor penalties. Uploading the suggested documents will unblock these questions.
        </p>

        {Object.keys(gapGroups).length === 0 ? (
          <p style={{ color: 'var(--ok-fg)', fontSize: '14px', fontWeight: '500' }}>✓ All questions have supporting evidence! No open compliance gaps.</p>
        ) : (
          Object.entries(gapGroups).map(([action, qList], idx) => (
            <div key={idx} className="card-surface" style={{ borderLeft: '2px solid var(--gap-fg)', marginBottom: '12px' }}>
              <div style={{ fontWeight: '600', color: 'var(--gap-fg)', marginBottom: '6px', fontSize: '14px' }}>
                Action: {action}
              </div>
              <div style={{ fontSize: '13px', color: 'var(--ink-2)' }}>
                <strong>Unblocks Questions:</strong>
                <ul style={{ paddingLeft: '20px', marginTop: '4px' }}>
                  {qList.map((q) => (
                    <li key={q.id} style={{ marginBottom: '2px' }}>
                      <span className="mono-text" style={{ color: 'var(--ink)' }}>{q.question_ref}</span>: {q.question_text}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Summary line above Daytona Audit Trail Panel */}
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: '13px', color: 'var(--ink-2)', marginBottom: '12px', fontWeight: '500' }}>
        3 agents · {sandboxEvents.length} events · sandbox {run?.sandbox_id || 'sbx_default'}
      </div>

      {/* Dark Sandbox Audit Trail Panel */}
      <div className="sandbox-audit-trail-panel">
        <div className="sandbox-audit-trail-header">
          <span className="sandbox-audit-trail-title">Sandboxed Execution — Daytona Audit Trail</span>
          <button className="btn btn-secondary" onClick={() => setShowCode(!showCode)} style={{ fontSize: '12px', padding: '4px 10px' }}>
            {showCode ? 'Hide Generated Code' : 'View Generated Code'}
          </button>
        </div>

        {showCode && (
          <div style={{ marginBottom: '16px', background: '#171717', border: '1px solid #262626', borderRadius: '6px', padding: '12px', fontFamily: 'var(--font-mono)', fontSize: '12.5px', color: '#5EEAD4', overflowX: 'auto' }}>
            <pre>{`# Generated Python executed inside Daytona sandbox container
import openpyxl, json
wb = openpyxl.load_workbook('/tmp/in/questionnaire.xlsx', data_only=True)
sheet = wb.active
res = []
for r in range(5, sheet.max_row + 1):
    q = sheet.cell(row=r, column=2).value
    if q:
        res.append({"ref": f"Q-{r-4:02d}", "question": q, "row": r, "answer_col": "D", "evidence_col": "E"})
print(json.dumps(res))`}</pre>
          </div>
        )}

        <div className="sandbox-terminal-window">
          {sandboxEvents.length === 0 ? (
            <div className="sandbox-terminal-line">
              <span className="sandbox-terminal-event">[{run?.status || 'pending'}]</span> Waiting for sandbox container provisioning and event logging...
            </div>
          ) : (
            sandboxEvents.map((evt, idx) => {
              let parsedDetails = evt.details_json;
              try {
                const obj = JSON.parse(evt.details_json);
                parsedDetails = Object.entries(obj)
                  .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`)
                  .join(' ');
              } catch (e) {
                // use raw string
              }

              const agentTag = evt.agent ? String(evt.agent).toLowerCase() : null;
              const isAccentAgent = agentTag === 'parser' || agentTag === 'writer';
              const isAnswerAgent = agentTag === 'answer';

              return (
                <div key={idx} className="sandbox-terminal-line">
                  {agentTag && (
                    <span style={{
                      color: isAccentAgent ? 'var(--accent)' : isAnswerAgent ? 'var(--ink-3)' : 'inherit',
                      fontFamily: 'var(--font-mono)',
                      fontSize: '11px',
                      textTransform: 'uppercase',
                      marginRight: '6px',
                      fontWeight: 'bold',
                    }}>
                      [{agentTag}]
                    </span>
                  )}
                  <span className="sandbox-terminal-event">[{evt.event_type || 'event'}]</span> {parsedDetails}
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
