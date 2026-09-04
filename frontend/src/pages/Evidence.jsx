import { useState } from 'react';
import { api, money, plainReason } from '../lib/api.js';
import { ProofPage } from '../components/Shell.jsx';
import {
  Async, Button, CodeBlock, Disclosure, ErrorLine, Markdown, Marginal, Note, Panel,
  Skeleton, useAsync,
} from '../components/ui.jsx';

// Sheet 05 - the reports. This is the last sheet in the set, so it is where the
// arrangement drawing belongs: the old version built it out of nested CSS boxes,
// which is a diagram made of chrome. It is an SVG now, drawn in hairlines with
// one crimson mark, and 160 lines of stylesheet went with the boxes.

const REPRO = `git clone https://github.com/SUMEET1000/reserve-gate && cd reserve-gate
pip install -r requirements.txt
pytest && python harness/run_eval.py && python -m src.buyer --scripted --overspend`;

// A model that did not answer gets a dash in every column it has no value for,
// so it can never be read as one that agreed.
const EMPTY = '—';

const withoutAsciiShape = text => text.replace(/## Shape\s+```[\s\S]*?```\s*/, '');

// The headline figures are read out of the committed report further down this
// same page rather than written here. Two copies of one measurement drift, and
// this copy would drift silently. A wording change in the report makes this
// return null and the panel says so - a missing summary is honest, a stale one
// is not.
function headline(report = '') {
  const cases = report.match(/^#[^\n]*?(\d+)\s+adversarial cases/m);
  const allow = report.match(/\*\*False-allow:\s*(\d+)\.\*\*/);
  const block = report.match(/False-block:\s*(\d+)\s+of\s+(\d+)/);
  if (!cases || !allow || !block) return null;
  return { cases: cases[1], allow: allow[1], block: block[1], passable: block[2] };
}

// The number language of the landing page's gate-off readings, so a figure
// means the same thing on both halves of the drawing.
function Figure({ value, label, tone }) {
  return (
    <div className="figure">
      <p className={`figure__value ${tone || ''}`}>{value}</p>
      <p className="figure__label">{label}</p>
    </div>
  );
}

// The arrangement, drawn rather than boxed. Three sources enter one gate, the
// gate has one decision in it, three outcomes leave, and every one of them
// writes to the chain running along the bottom. Crimson is spent only on the
// boundary itself, because the boundary is the whole claim.
function ArchitectureFlow() {
  const hair = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.5 };
  const narrowJoin = (
    <svg className="arrangement__join arrangement__join--narrow" viewBox="0 0 600 46" aria-hidden="true"
         preserveAspectRatio="none">
      <path d="M300 0v46" {...hair} />
      <path d="m294 40 6 6 6-6" {...hair} />
    </svg>
  );
  const SOURCES = [
    ['Buyer agent', 'Scripted or model-driven MCP call'],
    ['MCP desktop client', 'Bearer-authenticated remote transport'],
    ["Judge's browser", 'Cookie-scoped sandbox, no bearer token'],
  ];
  const CORE = [
    ['Money tools', 'Create and capture enter policy. Fetches are logged reads.'],
    ['Deterministic policy', 'Budget, expiry, revocation, approval, idempotency and reconciliation.'],
    ['Operator controls', 'Approve, revoke and unfreeze require a separate admin token.'],
  ];
  const OUT = [
    ['BLOCK', 'Return a refusal. Nothing goes upstream.', 'is-block'],
    ['HOLD', 'Wait for a human approval decision.', 'is-hold'],
    ['ALLOW', 'Forward to Razorpay test-mode Orders and Payments.', 'is-allow'],
  ];
  return (
    <figure className="arrangement" aria-labelledby="system-flow-title">
      <figcaption id="system-flow-title">
        Shape — every request passes through one gate
      </figcaption>

      <div className="arrangement__rank">
        {SOURCES.map(([t, s]) => (
          <div key={t} className="arrangement__cell"><b>{t}</b><span>{s}</span></div>
        ))}
      </div>

      {/* Three runs converging on one boundary. Drawn, so the convergence is
          visible as convergence and not implied by two stacked rows. */}
      <svg className="arrangement__join arrangement__join--wide" viewBox="0 0 600 46" aria-hidden="true"
           preserveAspectRatio="none">
        <path d="M100 0v18h200v28M300 0v46M500 0v18H300" {...hair} />
        <path d="m294 40 6 6 6-6" {...hair} />
      </svg>
      {narrowJoin}

      <div className="arrangement__gate">
        <div className="arrangement__gate-head">
          <b>reserve-gate</b>
          <span>one policy boundary</span>
        </div>
        <div className="arrangement__gate-core">
          {CORE.map(([t, s]) => (
            <div key={t}><b>{t}</b><p>{s}</p></div>
          ))}
        </div>
      </div>

      <svg className="arrangement__join arrangement__join--wide" viewBox="0 0 600 46" aria-hidden="true"
           preserveAspectRatio="none">
        <path d="M300 0v18h-200v28M300 18v28M300 18h200v28" {...hair} />
        {[100, 300, 500].map(x => (
          <path key={x} d={`m${x - 6} 40 6 6 6-6`} {...hair} />
        ))}
      </svg>
      {narrowJoin}

      <div className="arrangement__rank">
        {OUT.map(([t, s, tone]) => (
          <div key={t} className={`arrangement__cell ${tone}`}>
            <b>{t}</b><span>{s}</span>
          </div>
        ))}
      </div>

      <p className="arrangement__audit">
        Every outcome appends one linked record to the audit chain
      </p>
    </figure>
  );
}

export default function Evidence() {
  // One fetch feeds every panel. Splitting it would be four reads of the same
  // committed documents on every visit.
  const evidence = useAsync(() => api('/api/evidence'));
  const [tamper, setTamper] = useState(null);
  const [busy, setBusy] = useState(false);

  async function flipAByte() {
    setBusy(true);
    setTamper({ loading: true });
    try {
      setTamper({ data: await api('/api/tamper', {}) });
    } catch (e) {
      setTamper({ error: e.message });
    } finally {
      setBusy(false);
    }
  }

  const h = headline(evidence.data?.eval_report);

  return (
    <ProofPage
      current="/evidence"
      title="Empirical verification and evidence"
      lede={'The security guarantee is that nothing which should have been refused ever gets through. '
        + 'A single counterexample destroys that claim, so this page provides the '
        + 'verifiable reports and tools to test it.'}
      stats={h ? [['Cases', h.cases], ['False allow', h.allow]] : []}
      footer={'One thing this does not protect — reserve-gate limits what the AI can spend, not '
        + 'what the shop owner can. Anyone holding the raw Razorpay key can skip this gate '
        + 'entirely and pay directly. The whole design assumes the AI is given a reserve-gate '
        + 'key and never the real one.'}
    >
      <Panel
        title="The claim, in four numbers"
        intro="Every figure here is read out of the committed reports further down this page, so
               this summary cannot state something they do not."
      >
        <Async state={evidence} height="8rem">
          {e => {
            const hd = headline(e.eval_report);
            if (!hd) {
              return <Note>The committed report is not in the shape this summary reads.</Note>;
            }
            const answered = (e.models || []).filter(m => m.status === 'ok');
            const refused = answered.filter(m => m.outcome === 'BLOCK').length;
            return (
              <div className="stagger figures">
                <Figure value={hd.cases} label="attack purchases tested" />
                <Figure
                  value={hd.allow}
                  label="got through that should not have"
                  tone={+hd.allow === 0 ? 'text-allow' : 'text-block'}
                />
                <Figure value={`${hd.block}/${hd.passable}`}
                        label="honest purchases wrongly refused" />
                <Figure
                  value={e.models?.length ? `${refused}/${e.models.length}` : EMPTY}
                  label="models refused the same over-cap purchase"
                />
              </div>
            );
          }}
        </Async>
      </Panel>

      <Panel
        title="Prove the record cannot be edited"
        intro="Every line of the log carries a fingerprint of the line before it. Press the
               button and one single character is changed, deep inside the file. Watch the
               check find it."
      >
        <Async state={evidence} height="5rem">
          {e => (
            <div className={`reading is-${e.chain.verified ? 'allow' : 'block'}`}>
              <h3>{e.chain.verified ? 'Intact' : `Broken at line ${e.chain.bad_line}`}</h3>
              <p className="reading__body">
                {e.chain.verified
                  ? `All ${e.chain.records} records link up correctly.`
                  : 'One record no longer matches the one before it.'}
              </p>
              <p className="reading__hash">final fingerprint {e.chain.tail}</p>
            </div>
          )}
        </Async>

        <Button variant="primary" className="mt-5" onClick={flipAByte} disabled={busy}>
          {busy ? 'Changing one character…' : 'Change one character and check again'}
        </Button>

        {tamper?.loading && <Skeleton height="6rem" />}
        {tamper?.error && <ErrorLine>{tamper.error}</ErrorLine>}
        {tamper?.data && (
          <div className="ba-pair">
            <div className="reading is-allow">
              <h3>Before</h3>
              <p className="reading__body">
                {tamper.data.before.verified
                  ? 'The record was intact.'
                  : 'Broken at line ' + tamper.data.before.bad_line + '.'}
              </p>
            </div>
            <div className="reading is-block">
              <h3>After one character changed on line {tamper.data.edited_line}</h3>
              <p className="reading__body">
                {tamper.data.after.verified
                  ? 'Still intact, which would be the failure.'
                  : 'Caught, at line ' + tamper.data.after.bad_line + '.'}
              </p>
              <Note className="mt-2">{tamper.data.note}</Note>
            </div>
          </div>
        )}

        <Disclosure className="mt-6" summary="Why this is not sealed with a secret key">
          <Note>
            A secret would have to live on the same machine as the program writing the log, so
            anyone able to rewrite the file would already hold it. Worse, checking the log would
            then need that secret, and the entire point is that a stranger with a copy of this
            repository can check it themselves. Instead the final fingerprint is published in
            the committed report, so a rewritten log gives a different answer and the
            disagreement is public.
          </Note>
        </Disclosure>
      </Panel>

      <Panel
        title="Run it yourself, in three commands"
        intro="No keys, no network, no Docker. Every secret is read only at the moment it is
               needed, so a fresh copy with nothing configured still runs the whole test set."
      >
        <CodeBlock code={REPRO} />
      </Panel>

      <Panel
        title="What this does not do"
        intro="Read from the repository's own README, so there is one copy of this list and it
               cannot quietly get shorter."
      >
        <div className="limits">
          {[
            ['Test mode', 'The payment proof uses Razorpay test mode. No real money moves.'],
            ['Ephemeral demo', 'A sleeping or redeployed free server can reset browser demo data.'],
            ['Key boundary', 'An AI holding the raw Razorpay key could bypass this gate entirely.'],
          ].map(([title, body]) => (
            <article key={title}>
              <Marginal>{title}</Marginal>
              <p>{body}</p>
            </article>
          ))}
        </div>
        <Disclosure className="mt-5" summary="Read every limitation" hint="from README">
          <Async state={evidence} height="10rem">{e => <Markdown text={e.limitations} />}</Async>
        </Disclosure>
      </Panel>

      {/* Six models, one prompt, one tool. What varies is the model; what does
          not vary is the verdict, which is the point - the gate is the same
          deterministic code whichever model is proposing at it. A model that
          never answered is never shown as agreeing; its status takes the reason
          column and the rest are em-dashes. */}
      <Panel
        title="Six models, one refusal"
        intro="The same over-cap purchase and the same order tool were sent to six models, and
               whatever each proposed was fed through the real ledger. The gate does not know
               which model it is talking to."
      >
        <Async state={evidence} height="12rem">
          {e => (e.models && e.models.length ? (
            <>
              {/* The 900 px floor is a readable canvas, not a preference: below it
                  the provider and rule headers break inside a word. */}
              <div className="table-scroll">
                <table className="sheet-table is-wide">
                  <thead>
                    <tr>
                      {['model', 'proposed', 'outcome', 'rule', 'what the gate said'].map(x => (
                        <th key={x}>{x}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {e.models.map(m => {
                      const answered = m.status === 'ok';
                      const tone = { ALLOW: 'is-allow', BLOCK: 'is-block', HOLD: 'is-hold' };
                      return (
                        <tr key={m.provider}>
                          <td>
                            <strong>{m.provider}</strong>
                            <code className="is-faint">{m.model}</code>
                          </td>
                          <td className="is-num">{answered ? money(m.proposed?.amount) : EMPTY}</td>
                          <td>
                            {answered
                              ? <span className={`tag ${tone[m.outcome] || ''}`}>{m.outcome}</span>
                              : EMPTY}
                          </td>
                          <td className="is-num">{answered ? m.rule : EMPTY}</td>
                          <td className={answered ? '' : 'is-faint'}>
                            {answered
                              ? plainReason(m, m.proposed?.amount, m.proposed?.currency)
                              : m.status}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <Note className="mt-4">
                Run it again with <code>python harness/multi_model.py</code>.
                It exits non-zero unless every model answered, so a silent absence cannot be read
                as agreement.
              </Note>
            </>
          ) : (
            <Note>
              No model run is committed in this copy. Run
              {' '}<code>python harness/multi_model.py</code> to make one.
            </Note>
          ))}
        </Async>
      </Panel>

      <Panel
        title="The full reports"
        intro="Everything above in its raw, committed form. These are long on purpose because they are
               meant to be checked, not read."
      >
        <Disclosure summary="The test results" hint="150 purchases">
          <Async state={evidence} height="20rem">{e => <Markdown text={e.eval_report} />}</Async>
        </Disclosure>
        <Disclosure summary="Every rule removed in turn" hint="16 rules">
          <Note className="mb-4">
            Try any row live on the <a href="/mutate">remove a rule</a> page.
          </Note>
          <Async state={evidence} height="16rem">{e => <Markdown text={e.mutation_report} />}</Async>
        </Disclosure>
        <Disclosure summary="How the whole thing is put together">
          <Async state={evidence} height="16rem">{e => <>
            <ArchitectureFlow />
            <Markdown text={withoutAsciiShape(e.architecture)} />
          </>}</Async>
        </Disclosure>
      </Panel>
    </ProofPage>
  );
}
