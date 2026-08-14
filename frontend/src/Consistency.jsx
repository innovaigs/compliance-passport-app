import React, { useState, useEffect } from 'react';

export default function Consistency() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch('/api/consistency')
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
        return res.json();
      })
      .then((d) => {
        setData(d);
        setLoading(false);
      })
      .catch((err) => {
        console.error('Failed to load consistency data:', err);
        setError(err.message);
        setLoading(false);
      });
  }, []);

  if (loading) {
    return (
      <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--ink-2)', fontFamily: 'var(--font-mono)' }}>
        Comparing answer consistency across buyers...
      </div>
    );
  }

  if (error) {
    return (
      <div className="card-surface" style={{ padding: '24px', borderColor: 'var(--gap-border)', background: 'var(--gap-bg)' }}>
        <h3 style={{ color: 'var(--gap-fg)', marginBottom: '8px' }}>Failed to load consistency report</h3>
        <p style={{ color: 'var(--ink-2)', fontSize: '14px' }}>{error}</p>
      </div>
    );
  }

  const totalAnswers = data?.total_answers ?? 0;
  const totalBuyers = data?.total_buyers ?? 0;
  const contradictions = data?.contradictions ?? [];
  const drifts = data?.drifts ?? [];
  const checkedPairs = data?.checked_pairs ?? 0;
  const droppedPairs = data?.dropped_pairs ?? 0;

  const renderSeverityPill = (severity, verdict) => {
    let bg = 'var(--ok-bg)';
    let border = 'var(--ok-border)';
    let fg = 'var(--ok-fg)';

    if (verdict === 'CONTRADICTION' || severity === 'high') {
      bg = 'var(--gap-bg)';
      border = 'var(--gap-border)';
      fg = 'var(--gap-fg)';
    } else if (verdict === 'DRIFT' || severity === 'medium') {
      bg = 'var(--warn-bg)';
      border = 'var(--warn-border)';
      fg = 'var(--warn-fg)';
    }

    return (
      <span
        style={{
          display: 'inline-block',
          padding: '2px 10px',
          borderRadius: '12px',
          fontSize: '11px',
          fontWeight: '600',
          fontFamily: 'var(--font-mono)',
          textTransform: 'uppercase',
          letterSpacing: '0.04em',
          backgroundColor: bg,
          border: `1px solid ${border}`,
          color: fg,
        }}
      >
        {verdict || severity}
      </span>
    );
  };

  const renderCard = (item, idx) => (
    <div
      key={idx}
      className="card-surface"
      style={{
        marginBottom: '20px',
        padding: '20px',
        border: `1px solid ${item.verdict === 'CONTRADICTION' ? 'var(--gap-border)' : 'var(--warn-border)'}`,
        borderRadius: 'var(--radius)',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '12px', gap: '12px' }}>
        <div>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '8px', flexWrap: 'wrap' }}>
            <span
              style={{
                fontFamily: 'var(--font-mono)',
                fontSize: '11px',
                padding: '2px 8px',
                borderRadius: 'var(--radius-sm)',
                backgroundColor: 'var(--surface-sunken)',
                border: '1px solid var(--border)',
                color: 'var(--ink)',
              }}
            >
              {item.buyer_1}
            </span>
            <span style={{ color: 'var(--ink-3)', fontSize: '12px' }}>vs</span>
            <span
              style={{
                fontFamily: 'var(--font-mono)',
                fontSize: '11px',
                padding: '2px 8px',
                borderRadius: 'var(--radius-sm)',
                backgroundColor: 'var(--surface-sunken)',
                border: '1px solid var(--border)',
                color: 'var(--ink)',
              }}
            >
              {item.buyer_2}
            </span>
          </div>

          <div style={{ fontWeight: '600', fontSize: '15px', color: 'var(--ink)' }}>
            <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--ink-3)', marginRight: '8px', fontSize: '13px' }}>
              {item.ref_1} / {item.ref_2}
            </span>
            {item.matched_question_text || item.question_text_1}
          </div>
        </div>

        <div>{renderSeverityPill(item.severity, item.verdict)}</div>
      </div>

      {/* Side by Side on Desktop, Stacked on Mobile */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
          gap: '16px',
          marginTop: '16px',
          marginBottom: '16px',
        }}
      >
        <div
          style={{
            padding: '14px',
            backgroundColor: 'var(--surface-sunken)',
            borderRadius: 'var(--radius-sm)',
            border: '1px solid var(--border)',
          }}
        >
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--ink-3)', marginBottom: '6px', textTransform: 'uppercase' }}>
            {item.buyer_1} ({item.ref_1})
          </div>
          <div style={{ fontSize: '13.5px', color: 'var(--ink)', lineHeight: '1.5' }}>
            {item.answer_1}
          </div>
        </div>

        <div
          style={{
            padding: '14px',
            backgroundColor: 'var(--surface-sunken)',
            borderRadius: 'var(--radius-sm)',
            border: '1px solid var(--border)',
          }}
        >
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--ink-3)', marginBottom: '6px', textTransform: 'uppercase' }}>
            {item.buyer_2} ({item.ref_2})
          </div>
          <div style={{ fontSize: '13.5px', color: 'var(--ink)', lineHeight: '1.5' }}>
            {item.answer_2}
          </div>
        </div>
      </div>

      {/* Explanation in --ink-2 */}
      <div style={{ fontSize: '13px', color: 'var(--ink-2)', borderTop: '1px solid var(--border)', paddingTop: '10px', marginTop: '10px' }}>
        <strong>Auditor Analysis:</strong> {item.explanation}
      </div>
    </div>
  );

  return (
    <div style={{ maxWidth: '1000px', margin: '0 auto', padding: '24px 0' }}>
      <div style={{ marginBottom: '24px' }}>
        <h2 style={{ fontSize: '24px', fontWeight: '700', color: 'var(--ink)', marginBottom: '6px' }}>
          Answer consistency across buyers
        </h2>
        <div style={{ fontSize: '14px', color: 'var(--ink-2)', fontFamily: 'var(--font-mono)' }}>
          {totalAnswers} answers compared across {totalBuyers} buyers · {contradictions.length} contradictions · {drifts.length} drift
          {droppedPairs > 0 ? ` (capped at ${checkedPairs} pairs, ${droppedPairs} dropped)` : ''}
        </div>
      </div>

      {/* Contradictions first */}
      {contradictions.length > 0 && (
        <div style={{ marginBottom: '28px' }}>
          <h3 style={{ fontSize: '16px', fontWeight: '600', color: 'var(--gap-fg)', marginBottom: '14px', textTransform: 'uppercase', letterSpacing: '0.04em', fontFamily: 'var(--font-mono)' }}>
            Contradictions ({contradictions.length})
          </h3>
          {contradictions.map((item, idx) => renderCard(item, `c-${idx}`))}
        </div>
      )}

      {/* Drift next */}
      {drifts.length > 0 && (
        <div style={{ marginBottom: '28px' }}>
          <h3 style={{ fontSize: '16px', fontWeight: '600', color: 'var(--warn-fg)', marginBottom: '14px', textTransform: 'uppercase', letterSpacing: '0.04em', fontFamily: 'var(--font-mono)' }}>
            Inconsistency Drift ({drifts.length})
          </h3>
          {drifts.map((item, idx) => renderCard(item, `d-${idx}`))}
        </div>
      )}

      {/* Positive result if 0 contradictions */}
      {contradictions.length === 0 && (
        <div
          className="card-surface"
          style={{
            padding: '24px',
            border: '1px solid var(--ok-border)',
            backgroundColor: 'var(--ok-bg)',
            borderRadius: 'var(--radius)',
            marginBottom: '24px',
          }}
        >
          <div style={{ fontWeight: '600', color: 'var(--ok-fg)', fontSize: '16px', marginBottom: '4px' }}>
            {totalAnswers} answers compared across {totalBuyers} buyers. No contradictions found.
          </div>
          <div style={{ color: 'var(--ink-2)', fontSize: '13.5px' }}>
            All cross-buyer disclosures are consistent with zero material factual conflicts detected.
          </div>
        </div>
      )}
    </div>
  );
}
