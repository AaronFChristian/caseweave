import { useState, useEffect, useCallback } from 'react'
import './App.css'

const API = 'http://localhost:8123'

/* ------------------------------------------------------------------
   Glossary. Persona A (investigators) knows all of this cold; Persona B
   (an interviewer / reviewer seeing it for the first time) may not.
   Definitions are opt-in via the ⓘ affordance so they never slow down
   someone who already knows the domain.
------------------------------------------------------------------- */
const GLOSSARY = {
  SAR: 'Suspicious Activity Report. A filing submitted to FinCEN, a US federal regulator, when a bank believes activity may indicate financial crime. The narrative section is free text and is the part law enforcement actually reads.',
  structuring:
    'Deliberately splitting cash transactions to stay under the $10,000 reporting threshold. Structuring is itself a federal offence, independent of where the money came from.',
  'false positive':
    'An alert that turns out to have an innocent explanation. Published analysis puts transaction-monitoring false positives at 90–95%, which is why triage exists.',
  'fact ID':
    'Every piece of evidence gets an ID like F-003 before the model writes anything. The narrative model may only make claims it can tag with one of these IDs.',
  'attribution coverage':
    'The share of sentences in a drafted narrative that cite a real fact AND survive an entailment check that the fact actually supports the claim. Below 90%, the system refuses to produce a narrative.',
  'evidence gap':
    'The system declined to draft. Not an error — it means the assembled evidence does not support a defensible filing, so it returns what is missing instead of guessing.',
  typology:
    'A recognised pattern of money laundering behaviour — structuring, layering, mule networks, and so on. Each detection rule maps to one.',
}

function Term({ k, children }) {
  const [open, setOpen] = useState(false)
  return (
    <span className="term-wrap">
      <button
        className="term"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        aria-label={`What is ${k}?`}
      >
        {children || k}
        <span className="term-mark">ⓘ</span>
      </button>
      {open && (
        <span className="term-pop" role="tooltip">
          {GLOSSARY[k]}
          <button className="term-close" onClick={() => setOpen(false)}>close</button>
        </span>
      )}
    </span>
  )
}

/* ------------------------------------------------------------------
   Pipeline. The single most important addition: it makes the agent's
   work legible. Someone watching sees six discrete stages advance,
   rather than a spinner followed by a wall of text.
------------------------------------------------------------------- */
const STAGES = [
  {
    key: 'alert',
    label: 'Alert',
    blurb: 'A detection rule fired on this account. Rules are deterministic SQL and graph queries — no model involved, so every alert can be explained in one sentence.',
  },
  {
    key: 'triage',
    label: 'Triage',
    blurb: 'A cheap model (Haiku) reads the alert and the customer profile and decides: investigate, or close with a recorded reason. This is the only place a model makes a routing decision.',
  },
  {
    key: 'evidence',
    label: 'Evidence',
    blurb: 'Facts are pulled from the transaction store, the counterparty graph, and the regulatory corpus. Each gets an ID. The ledger is then FROZEN — nothing can be added once drafting starts.',
  },
  {
    key: 'draft',
    label: 'Draft',
    blurb: 'A stronger model (Sonnet) writes the narrative. It is shown ONLY the frozen ledger — never a raw database row — and every sentence must cite a fact ID.',
  },
  {
    key: 'verify',
    label: 'Verify',
    blurb: 'Each sentence is checked: does it cite a real fact, and does that fact actually support the claim? Below 90% coverage the narrative is refused outright.',
  },
  {
    key: 'review',
    label: 'Review',
    blurb: 'A human approves, edits, or rejects — with a reason code from a controlled list. The model never files anything on its own.',
  },
]

const STATUS_TO_STAGE = {
  loaded: 1,
  triaged: 2,
  evidenced: 3,
  drafted: 4,
  ready_for_review: 5,
  refused: 5,
  closed: 2,
}

function Pipeline({ status, evidenceReady, running, onPick, picked }) {
  const reached = status ? STATUS_TO_STAGE[status] ?? 0 : evidenceReady ? 1 : 0
  return (
    <div className="pipeline" role="list" aria-label="Investigation pipeline">
      {STAGES.map((s, i) => {
        const done = i < reached
        const active = running && i === reached
        return (
          <button
            key={s.key}
            role="listitem"
            className={`stage ${done ? 'done' : ''} ${active ? 'active' : ''} ${picked === i ? 'picked' : ''}`}
            onClick={() => onPick(picked === i ? null : i)}
            aria-label={`${s.label} — ${done ? 'complete' : active ? 'running' : 'pending'}`}
          >
            <span className="stage-dot" />
            <span className="stage-label">{s.label}</span>
          </button>
        )
      })}
    </div>
  )
}

/* ---------------------------- queue ---------------------------- */
function QueuePane({ alerts, selected, onSelect, summary, demoMode }) {
  return (
    <section className="pane">
      <div className="pane-head">
        <h2>Case Queue</h2>
        <span className="pane-count">
          {summary ? `${summary.total_alerts} open · ${summary.by_rule.length} rules` : ''}
        </span>
      </div>
      <ul className="queue-list">
        {alerts.map(a => (
          <li key={a.alert_id}>
            <button
              className={`queue-row ${a.alert_id === selected ? 'selected' : ''}`}
              onClick={() => onSelect(a.alert_id)}
            >
              <span className="queue-tab">{a.alert_id}</span>
              <span className="queue-info">
                <span className="queue-rule">{a.rule_code} · {a.rule_name}</span>
                <span className="queue-reason">{a.trigger_reason}</span>
              </span>
              <span className="queue-right">
                <span className="queue-amount">
                  ${a.total_amount?.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                </span>
                {demoMode && (
                  <span className={`truth ${a.gt_label ? 'real' : 'noise'}`}>
                    {a.gt_label ? 'real' : 'noise'}
                  </span>
                )}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}

/* ------------------------- narrative --------------------------- */
function Narrative({ text, onHover, activeIds }) {
  const parts = text.split(/(\[F-\d{3}(?:,\s*F-\d{3})*\])/g)
  return (
    <p className="narrative-body">
      {parts.map((part, i) => {
        const ids = part.match(/F-\d{3}/g)
        if (!ids) return <span key={i}>{part}</span>
        const on = ids.some(id => activeIds?.includes(id))
        return (
          <button
            key={i}
            className={`exhibit ${on ? 'active' : ''}`}
            onMouseEnter={() => onHover(ids)}
            onMouseLeave={() => onHover(null)}
            onFocus={() => onHover(ids)}
            onBlur={() => onHover(null)}
          >
            {part}
          </button>
        )
      })}
    </p>
  )
}

function Ledger({ facts, litIds }) {
  const byKind = facts.reduce((acc, f) => ({ ...acc, [f.kind]: (acc[f.kind] || 0) + 1 }), {})
  return (
    <>
      <div className="ledger-legend">
        {Object.entries(byKind).map(([k, n]) => (
          <span key={k} className="legend-chip">{k.replace(/_/g, ' ')} · {n}</span>
        ))}
      </div>
      <ul className="ledger">
        {facts.map(f => (
          <li key={f.fact_id} className={`ledger-card ${litIds?.includes(f.fact_id) ? 'lit' : ''}`}>
            <span className="ledger-fact-id">{f.fact_id}</span>
            <span className="ledger-kind">{f.kind.replace(/_/g, ' ')}</span>
            <span className="ledger-summary">{f.summary}</span>
          </li>
        ))}
      </ul>
    </>
  )
}

/* ------------------------ case detail -------------------------- */
function CaseDetail({ alertId, alertMeta, demoMode }) {
  const [evidence, setEvidence] = useState(null)
  const [result, setResult] = useState(null)
  const [running, setRunning] = useState(false)
  const [litIds, setLitIds] = useState(null)
  const [error, setError] = useState(null)
  const [reasonCode, setReasonCode] = useState('')
  const [reasonCodes, setReasonCodes] = useState({})
  const [msg, setMsg] = useState(null)
  const [pickedStage, setPickedStage] = useState(null)

  useEffect(() => {
    setEvidence(null); setResult(null); setError(null); setMsg(null)
    setReasonCode(''); setPickedStage(null); setLitIds(null)
    fetch(`${API}/alerts/${alertId}/evidence`).then(r => r.json()).then(setEvidence).catch(() => {})
    fetch(`${API}/reason-codes`).then(r => r.json()).then(setReasonCodes).catch(() => {})
  }, [alertId])

  const run = useCallback(async () => {
    setRunning(true); setError(null); setPickedStage(null)
    try {
      const r = await fetch(`${API}/alerts/${alertId}/run-case`, { method: 'POST' })
      if (!r.ok) throw new Error(await r.text())
      setResult(await r.json())
    } catch (e) { setError(String(e)) } finally { setRunning(false) }
  }, [alertId])

  const decide = async decision => {
    if (!reasonCode) { setMsg('Pick a reason code first.'); return }
    const r = await fetch(`${API}/alerts/${alertId}/disposition`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision, reason_code: reasonCode, reviewer: 'aaron' }),
    })
    setMsg(r.ok ? `Recorded — ${decision} (${reasonCode.replace(/_/g, ' ')})` : `Failed: ${await r.text()}`)
  }

  const facts = result?.facts || evidence?.facts || []
  const refused = result?.status === 'refused'
  const options = refused
    ? reasonCodes.reject || []
    : [...(reasonCodes.approve || []), ...(reasonCodes.edit || []), ...(reasonCodes.reject || [])]

  return (
    <section className="pane">
      <div className="detail-head">
        <div>
          <h2>{alertId}</h2>
          <p className="detail-sub">{alertMeta?.rule_code} · {alertMeta?.rule_name}</p>
        </div>
        <button className="btn primary" onClick={run} disabled={running}>
          {running ? 'Running pipeline…' : result ? 'Re-run pipeline' : 'Run investigation'}
        </button>
      </div>

      <Pipeline
        status={result?.status}
        evidenceReady={!!evidence}
        running={running}
        onPick={setPickedStage}
        picked={pickedStage}
      />

      <div className="stage-detail">
        {pickedStage != null ? (
          <p><strong>{STAGES[pickedStage].label}.</strong> {STAGES[pickedStage].blurb}</p>
        ) : running ? (
          <p className="muted">Running — triage, then evidence freeze, then draft, then verification.</p>
        ) : result ? (
          refused ? (
            <p><strong>Refused.</strong> Coverage came in at {(result.attribution_coverage * 100).toFixed(0)}%,
            below the 90% bar. The system returned what evidence is missing instead of drafting something it
            could not defend. <em>This is the behaviour the whole architecture exists to produce.</em></p>
          ) : (
            <p><strong>Ready for review.</strong> Every sentence below cites evidence that existed before
            drafting began and survived an entailment check. Hover a citation to light up its source.</p>
          )
        ) : (
          <p className="muted">
            Evidence is already assembled below — that part is deterministic and costs nothing.
            Press <strong>Run investigation</strong> to let the models triage and draft. Click any
            stage above to see what it does.
          </p>
        )}
      </div>

      <div className="body">
        {error && (
          <div className="callout error">
            <div className="callout-title">Run failed</div>{error}
          </div>
        )}

        {refused && (
          <div className="callout">
            <div className="callout-title">
              <Term k="evidence gap">Evidence gap</Term> — narrative withheld
            </div>
            The assembled evidence does not meet the <Term k="attribution coverage">attribution coverage</Term>
            {' '}threshold required for a defensible filing. An investigator gets the gap report below rather
            than a draft that reads convincingly but is not fully supported.
          </div>
        )}

        {demoMode && alertMeta && (
          <div className={`truth-reveal ${alertMeta.gt_label ? 'real' : 'noise'}`}>
            <span className="truth-label">Ground truth</span>
            {alertMeta.gt_label
              ? `Genuine ${alertMeta.gt_typology?.replace(/_/g, ' ')} planted by the generator — this alert should be investigated.`
              : 'Benign activity that legitimately trips the rule — this alert is noise and should close.'}
            <span className="truth-note">
              Only knowable because the data is synthetic. Nothing in the pipeline can read this.
            </span>
          </div>
        )}

        {result?.narrative_text && (
          <>
            <p className="section-label">
              <span>{refused ? 'Gap report' : 'Draft narrative'}</span>
              {result.attribution_coverage != null && (
                <span className={`coverage ${result.attribution_coverage >= 0.9 ? 'ok' : 'low'}`}>
                  {(result.attribution_coverage * 100).toFixed(0)}% attributed
                </span>
              )}
            </p>
            <Narrative text={result.narrative_text} onHover={setLitIds} activeIds={litIds} />
            {result.est_cost_usd != null && (
              <div className="cost-strip">
                cost ${result.est_cost_usd.toFixed(4)} · {result.facts?.length} facts ·
                {' '}{result.narrative_cited_ids?.length || 0} cited
              </div>
            )}
          </>
        )}

        <p className="section-label">
          <span>Evidence ledger — {facts.length} <Term k="fact ID">facts</Term></span>
          {result && <span className="frozen-tag">frozen before drafting</span>}
        </p>
        <Ledger facts={facts} litIds={litIds} />
      </div>

      <div className="signoff">
        <span className="signoff-label">Human decision</span>
        <select value={reasonCode} onChange={e => setReasonCode(e.target.value)}>
          <option value="">Reason code…</option>
          {options.map(c => <option key={c} value={c}>{c.replace(/_/g, ' ')}</option>)}
        </select>
        <button className="btn ghost" onClick={() => decide('approve')}>Approve</button>
        <button className="btn ghost" onClick={() => decide('edit')}>Edit</button>
        <button className="btn ghost" onClick={() => decide('reject')}>Reject</button>
        {msg && <span className="signoff-msg">{msg}</span>}
      </div>
    </section>
  )
}

/* ---------------------------- app ------------------------------ */
export default function App() {
  const [alerts, setAlerts] = useState([])
  const [summary, setSummary] = useState(null)
  const [selected, setSelected] = useState(null)
  const [down, setDown] = useState(false)
  const [primerOpen, setPrimerOpen] = useState(true)
  const [demoMode, setDemoMode] = useState(false)

  useEffect(() => {
    fetch(`${API}/alerts?limit=50`).then(r => r.json())
      .then(d => { setAlerts(d); if (d.length) setSelected(d[0].alert_id) })
      .catch(() => setDown(true))
    fetch(`${API}/queue/summary`).then(r => r.json()).then(setSummary).catch(() => {})
  }, [])

  if (down) {
    return (
      <div className="down">
        <h1 className="wordmark">Case<em>Weave</em></h1>
        <p>Can't reach the API at {API}.</p>
        <code>make api</code>
      </div>
    )
  }

  const meta = alerts.find(a => a.alert_id === selected)

  return (
    <div className="app">
      <header className="masthead">
        <div>
          <h1 className="wordmark">Case<em>Weave</em></h1>
          <p className="tagline">
            An AML investigation copilot that refuses to guess.
          </p>
        </div>
        <div className="masthead-controls">
          <label className="toggle">
            <input type="checkbox" checked={demoMode} onChange={e => setDemoMode(e.target.checked)} />
            <span>Reveal ground truth</span>
          </label>
          <button className="btn ghost sm" onClick={() => setPrimerOpen(o => !o)}>
            {primerOpen ? 'Hide primer' : 'What is this?'}
          </button>
        </div>
      </header>

      {primerOpen && summary && (
        <section className="primer">
          <div className="primer-grid">
            <div>
              <h3>The problem</h3>
              <p>
                Banks must file a <Term k="SAR">SAR</Term> when activity looks like financial crime.
                Monitoring systems flag far more than is real — in this dataset{' '}
                <strong>{(summary.false_positive_rate * 100).toFixed(0)}% of alerts are{' '}
                <Term k="false positive">noise</Term></strong>. Investigators spend 30+ minutes per
                filing manually assembling records and writing the narrative by hand.
              </p>
            </div>
            <div>
              <h3>Why an LLM alone fails</h3>
              <p>
                A SAR narrative is a legal document submitted to a federal regulator. A hallucinated
                sentence isn't a bad user experience — it's a false statement to the government.
                Fluent-but-unsupported prose is worse than no prose.
              </p>
            </div>
            <div>
              <h3>The approach</h3>
              <p>
                <strong>Evidence is deterministic; only prose is probabilistic.</strong> Facts are
                assembled and frozen with IDs <em>before</em> any model writes. The model sees only
                that ledger, must cite every claim, and a guardrail kills sentences that don't hold
                up. Under 90% <Term k="attribution coverage">coverage</Term>, it refuses to draft.
              </p>
            </div>
          </div>
          <div className="primer-stats">
            <span><strong>{summary.transactions_monitored?.toLocaleString()}</strong> transactions</span>
            <span><strong>{summary.parties_monitored}</strong> parties</span>
            <span><strong>{summary.total_alerts}</strong> alerts</span>
            <span><strong>{summary.by_rule.length}</strong> detection rules</span>
            <span className="primer-synthetic">All data synthetic</span>
          </div>
          <p className="primer-how">
            <strong>Try it:</strong> pick a case on the left, press <em>Run investigation</em>, then hover
            a <span className="exhibit inline">[F-00X]</span> citation in the narrative to light up the
            exact evidence behind that claim. Turn on <em>Reveal ground truth</em> to check the system's
            judgment against the planted answer.
          </p>
        </section>
      )}

      <main>
        <QueuePane
          alerts={alerts} selected={selected} onSelect={setSelected}
          summary={summary} demoMode={demoMode}
        />
        {selected && (
          <CaseDetail key={selected} alertId={selected} alertMeta={meta} demoMode={demoMode} />
        )}
      </main>
    </div>
  )
}
