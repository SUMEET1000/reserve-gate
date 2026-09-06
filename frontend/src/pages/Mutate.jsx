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

// The five ways a test purchase can come out wrong, each with the sentence a
// visitor needs to read the number beside it. Bare labels - "Wrong effect: 0",
// "Right answer, wrong reason: 4" - said neither what was counted nor whether 4
// was alarming, and two of these five are alarming while three are not.
//
// The last column is whether the number has to be zero. Only false-allow and a
// diverged twin are holes; the other three are the gate refusing correctly and
// being untidy about it, which is worth reporting and is not a failure.
const COUNTS = [
  ['false_allow', 'Money that got out',
   'A purchase the gate had to refuse, and allowed. The one number that must be zero.', true],
  ['twins', 'Hidden instructions that worked',
   'A product name carrying an instruction, decided differently from the same purchase '
   + 'without it. Must be zero, or the gate read the text.', true],
  ['wrong_rule', 'Refused by a different rule',
   'Right answer, another rule’s name on it. The money still stopped; this counts how '
   + 'often a second safeguard did the work.', false],
  ['wrong_effect', 'Payment message did the wrong thing',
   'A confirmation from Razorpay that should have been applied, ignored or rejected, and '
   + 'was handled the other way.', false],
  ['false_block', 'Good purchases refused',
   'A purchase that should have passed and did not. The cost of being strict — annoying, '
   + 'never dangerous.', false],
];

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
        <Note className="mb-4">
          Five ways one of the 150 test purchases can come out wrong. Two of them are
          dangerous and two are only untidy, so each row says which it is — a number
          above zero is not automatically bad news.
        </Note>
        <dl className="count-list is-explained">
          {COUNTS.map(([key, label, meaning, mustBeZero]) => {
            const n = result[key];
            return (
              <div key={key} className={mustBeZero && n > 0 ? 'is-bad' : ''}>
                <dt>
                  {label}
                  <span>{meaning}</span>
                </dt>
                <dd>{n}</dd>
              </div>
            );
          })}
        </dl>
      </Disclosure>
    </div>
  );
}

// A run's answer, under the button that asked for it. There is one of these in
// each panel: a single well under the second panel meant the control button at
// the top ran, said "Checking 150 purchases…", and then printed its answer a
// page and a half below under a heading about removing a rule - so from the
// control's own panel nothing happened at all.
function ResultWell({ busy, result, empty }) {
  return (
    <div className="result-well">
      {busy ? <Skeleton height="6rem" />
        : result?.error ? <ErrorLine>{result.error}</ErrorLine>
          : result?.data ? <Result result={result.data} />
            : <Note>{empty}</Note>}
    </div>
  );
}

export default function Mutate() {
  const mutations = useAsync(() => api('/api/mutations'));
  const [running, setRunning] = useState(null);
  // Kept per panel, so running a mutation does not erase the control run that
  // is the only thing making that mutation's result mean anything.
  const [results, setResults] = useState({});

  async function run(index, label) {
    if (running) return;
    const from = index === null ? 'baseline' : 'mutation';
    setRunning(label);
    setResults(rs => ({ ...rs, [from]: null }));
    try {
      const data = await api('/api/mutate', index === null ? {} : { index });
      setResults(rs => ({ ...rs, [from]: { data } }));
    } catch (e) {
      setResults(rs => ({ ...rs, [from]: { error: e.message } }));
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
        <ResultWell busy={running === 'baseline'} result={results.baseline}
                    empty="Not run yet. Press the button above — it takes about a second." />
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

        <ResultWell busy={!!running && running !== 'baseline'} result={results.mutation}
                    empty="No rule removed yet. Start with the control above, then pick one." />
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
