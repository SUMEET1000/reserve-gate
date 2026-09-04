import { useState } from 'react';
import { api } from '../lib/api.js';
import { ProofPage } from '../components/Shell.jsx';
import {
  Async, Button, Disclosure, ErrorLine, Markdown, Note, Panel, Skeleton, useAsync,
} from '../components/ui.jsx';

// Sheet 03 - the safety-net test. The result is drawn as a reading taken off
// the sheet, the same object the landing page uses for the gate-off figures:
// a heading that says what happened and the counts under it. It used to be a
// 3px coloured left border, which is colour doing the whole job on its own.

// The server names a guard by the rule id and the variable it guards, which is
// the language of the source file. Anything missing from this map falls through
// to the server's own label, so a guard added later shows up as itself rather
// than as nothing.
const PLAIN = {
  'R0 amount type': 'The price must be a whole number',
  'R0 amount bounds': 'The price must be a believable size',
  'R0 currency': 'It must be the same currency as your budget',
  'R1 block cap': 'It must fit in what is left of your budget',
  'R2 expiry': 'A budget past its end date buys nothing',
  'R3 already captured': 'One order cannot be charged twice',
  'R3 no reservation': 'Only an order this gate made can be charged',
  'R4 revocation': 'A cancelled budget refuses everything',
  'R5 per-call cap': 'No single purchase above your limit',
  'R6 velocity': 'Not too many purchases too quickly',
  'R7 replay': 'The same request sent twice is still one purchase',
  'R7 in flight': 'One purchase at a time, never two at once',
  'G15 tool allowlist': 'Only the payment actions we chose to offer',
  'G16 key conflict': 'The same receipt cannot name two different prices',
  'G4 frozen block': 'A frozen budget refuses everything',
  'approval hold': 'Anything big has to ask you first',
};

function verdict(r) {
  if (r.baseline) {
    return r.ok
      ? { good: true, head: 'Clean, exactly as it must be' }
      : { good: false, head: 'The test set itself is broken' };
  }
  return r.ok
    ? { good: true, head: 'Caught it' }
    : { good: false, head: 'Nothing noticed' };
}

function Result({ result }) {
  const v = verdict(result);
  const name = PLAIN[result.label] || result.label;
  return (
    <div className={`reading is-${v.good ? 'allow' : 'block'}`}>
      <h3>{v.head}</h3>
      <p className="reading__body">
        {result.baseline
          ? 'With every rule in place, all 150 test purchases came out right.'
          : v.good
            ? <>With <b>{name.toLowerCase()}</b> deleted, the test set found the difference.</>
            : <>Deleting <b>{name.toLowerCase()}</b> changed nothing, so no test depends on
                it. That is the alarming outcome, not the safe one.</>}
      </p>
      <p className="reading__foot">
        {result.cases} purchases re-checked in {result.seconds}s.{' '}
        <b className={result.false_allow ? 'text-block' : ''}>{result.false_allow}</b>{' '}
        got through that should not have.
      </p>
      {!result.baseline && result.caught_by && result.caught_by !== '-' && (
        <p className="reading__foot">
          When money still did not escape, this stopped it instead — <b>{result.caught_by}</b>.
        </p>
      )}
      <Disclosure className="mt-5" summary="The exact counts">
        <dl className="count-list">
          {[['Got through wrongly', result.false_allow],
            ['Right answer, wrong reason', result.wrong_rule],
            ['Wrong effect', result.wrong_effect],
            ['Hidden-instruction pairs that differed', result.twins],
            ['Refused when it should have passed', result.false_block]].map(([k, n]) => (
              <div key={k}><dt>{k}</dt><dd>{n}</dd></div>
            ))}
        </dl>
      </Disclosure>
    </div>
  );
}

export default function Mutate() {
  const mutations = useAsync(() => api('/api/mutations'));
  const [running, setRunning] = useState(null);
  const [result, setResult] = useState(null);

  async function run(index, label) {
    if (running) return;
    setRunning(label);
    setResult(null);
    try {
      setResult({ data: await api('/api/mutate', index === null ? {} : { index }) });
    } catch (e) {
      setResult({ error: e.message });
    } finally {
      setRunning(null);
    }
  }

  return (
    <ProofPage
      current="/mutate"
      title="Remove a rule and see if anyone notices"
      lede={'Mutation testing validates test suite sensitivity. If tests still pass '
        + 'after deleting a gate rule, they were never really testing it. Disabling a '
        + 'rule proves whether each defense is actively verified.'}
      stats={[['Rules removable', '16'], ['Cases replayed', '150']]}
      footer={'You are choosing from a fixed list, by number. Nothing you type is ever run as '
        + 'code, and the scoring happens in a separate process, so removing a rule here cannot '
        + 'affect anybody else using the site at the same moment.'}
    >
      <Panel
        title="First, the control"
        intro="Run it with nothing removed. All 150 test purchases must come out right. If they
               do not, every red result below could be blamed on a broken test set rather than
               on a missing rule, so this run is what makes the rest of the page mean anything."
      >
        <Button variant="primary" onClick={() => run(null, 'baseline')} disabled={!!running}>
          {running === 'baseline' ? 'Checking 150 purchases…' : 'Run it with nothing removed'}
        </Button>
      </Panel>

      <Panel
        title="Now take one out"
        intro="Pick a rule. It is deleted from a copy of the gate, and the same 150 test
               purchases are checked again without it. It takes about a second."
      >
        <div className="pick-grid">
          <Async state={mutations} height="12rem">
            {data => data.mutations.map(m => (
              <Button
                key={m.index}
                onClick={() => run(m.index, m.label)}
                disabled={!!running}
                className="pick"
                roll={false}
              >
                {running === m.label ? 'Checking…' : (PLAIN[m.label] || m.label)}
              </Button>
            ))}
          </Async>
        </div>

        <div className="result-well">
          {result?.error && <ErrorLine>{result.error}</ErrorLine>}
          {running && <Skeleton height="6rem" />}
          {!running && !result && (
            <Note>Nothing run yet. Start with the control above.</Note>
          )}
          {result?.data && <Result result={result.data} />}
        </div>
      </Panel>

      <Panel
        title="Every rule, already done for you"
        intro="You do not have to click sixteen buttons. This is the same exercise run over
               every rule in turn and committed to the repository, so a reader can check the
               numbers without running anything."
      >
        <Disclosure summary="Show the full table" hint="16 rules">
          <Note className="mb-4">
            A row where nothing got through is not a weak row. It means a second safeguard
            stopped the money once the first was gone, and the last column names which one.
          </Note>
          <Async state={mutations} height="14rem">
            {data => <Markdown text={data.report} />}
          </Async>
        </Disclosure>
      </Panel>
    </ProofPage>
  );
}
