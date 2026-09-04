import { api, money } from '../lib/api.js';
import { ProofPage } from '../components/Shell.jsx';
import { Async, Disclosure, Markdown, Note, Panel, useAsync } from '../components/ui.jsx';

// Sheet 05 - the control table. Every figure here is read live from the file
// the gate obeys, so the page cannot drift away from the settings it describes.
// That is the only reason it is worth showing at all, and it is why the numbers
// are set as measurements rather than as headline statistics.

// policy.yaml's keys are the config file's names, not a reader's. Anything
// missing from this map falls through to the raw key, which is how a setting
// added later shows up as itself rather than as nothing.
const LABELS = {
  reserved: ['Total budget', 'The most this AI can ever spend.'],
  currency: ['Currency', 'One budget, one currency. No conversions to argue about.'],
  expires_days: ['Valid for', 'After this many days the budget stops working.'],
  max_txn: ['Biggest single purchase', 'No one purchase may be larger than this.'],
  approval_over: ['Ask me above', 'Anything dearer waits for you to say yes.'],
  velocity_calls: ['Purchases allowed per window', 'Counting the refused ones too.'],
  velocity_window_minutes: ['Length of that window', 'In minutes.'],
  reservation_ttl_minutes: ['Unpaid order returns after', 'Minutes before the money comes back.'],
  derived_key_ttl_seconds: ['A repeated request counts as the same one for',
    'Seconds. Long enough to catch a retry, short enough to buy the same thing twice.'],
};
const MONEY_KEYS = new Set(['reserved', 'max_txn', 'approval_over']);

// The rule ids are how the audit log and the mutation page name each rule, so
// they stay on the page - but as a reference number beside a plain title, never
// as the title itself.
const PLAIN_TITLE = {
  R0: 'The price has to be a real price',
  R1: 'It has to fit in the budget',
  R2: 'The budget stops working on its end date',
  R3: 'One budget, many purchases',
  R4: 'You can cancel at any moment',
  R5: 'No single purchase above your limit',
  R6: 'Not too many purchases too quickly',
  R7: 'Asking twice does not buy twice',
  approval: 'Anything big has to ask you first',
};

export default function Rules() {
  const rules = useAsync(() => api('/api/rules'));
  const cfg = rules.data?.config;

  return (
    <ProofPage
      current="/rules"
      title="What stops the AI overspending?"
      lede={'Nine checks run before any purchase reaches Razorpay, and not one of them was '
        + 'invented here. Every number below is read live from the settings file the gate '
        + 'actually obeys, so this page cannot quietly drift away from the truth.'}
      // The strip carries the two figures this sheet is about, read from the
      // same request the table below is drawn from - never a second source.
      stats={cfg ? [
        ['Budget', money(cfg.reserved, cfg.currency)],
        ['Per purchase', money(cfg.max_txn, cfg.currency)],
        ['Checks', '09'],
      ] : [['Checks', '09']]}
      footer={'reserve-gate copies how Reserve Pay behaves, and does not use Reserve Pay, which '
        + 'Razorpay does not offer in test mode. Saying otherwise would be untrue and easy to '
        + 'catch.'}
    >
      <Panel
        title="Your current settings"
        intro="These are the live values. Change them for yourself on the guided demo, and every
               decision on this site follows the new ones straight away."
      >
        <Async state={rules} height="8rem">
          {r => (
            <dl className="spec-list">
              {Object.entries(r.config).map(([k, v]) => {
                const [name, hint] = LABELS[k] || [k, ''];
                return (
                  <div key={k}>
                    <dt>{name}</dt>
                    <dd className="spec-list__value">
                      {MONEY_KEYS.has(k) ? money(v, r.config.currency) : String(v)}
                    </dd>
                    {hint && <dd className="spec-list__hint">{hint}</dd>}
                  </div>
                );
              })}
            </dl>
          )}
        </Async>
      </Panel>

      <Panel
        title="The nine checks"
        intro="In the order the gate applies them. The short code beside each one is what the
               audit log prints when that check is the reason a purchase was refused."
      >
        <Async state={rules} height="16rem">
          {r => (
            <div className="rule-list">
              {(r.rules.rules || []).map(x => (
                <article key={x.id}>
                  {/* The id is the reference number in the margin, the way a
                      drawing keys a detail - not a heading in its own right. */}
                  <code className="rule-list__id">{x.id}</code>
                  <div>
                    <h3>{PLAIN_TITLE[x.id] || x.title}</h3>
                    <p>{x.plain || x.semantics}</p>
                    <Disclosure className="mt-4" summary="Source and exact wording">
                      <Note>{x.source}</Note>
                      {x.why && <Note className="mt-3">{x.why}</Note>}
                      {x.plain && (
                        <Note className="mt-4 border-t border-rule pt-3">
                          Precisely: {x.semantics}
                        </Note>
                      )}
                    </Disclosure>
                  </div>
                </article>
              ))}
            </div>
          )}
        </Async>
      </Panel>

      <Panel
        title="The safety rules underneath"
        intro="The nine checks decide. These six decide what happens when something goes wrong
               while deciding — a dropped connection, a crash, a reply that contradicts itself."
      >
        <Async state={rules} height="8rem">
          {r => (
            <>
              <dl className="guard-list">
                {(r.rules.guards || []).map(g => (
                  <div key={g.id}>
                    <dt>
                      {g.title}
                      <code>{g.id}</code>
                    </dt>
                    <dd>{g.plain || g.semantics}</dd>
                  </div>
                ))}
              </dl>
              <Disclosure className="mt-6" summary="The exact wording of all six">
                <dl className="guard-list is-exact">
                  {(r.rules.guards || []).map(g => (
                    <div key={g.id}>
                      <dt><code>{g.id}</code> {g.title}</dt>
                      <dd>{g.semantics}</dd>
                    </div>
                  ))}
                </dl>
              </Disclosure>
            </>
          )}
        </Async>
      </Panel>

      <Panel
        title="Where the attacks came from"
        intro="I wrote the tests, so I do not get to decide which attacks count. Every kind of
               attack this gate is tested against is named by somebody else — a published
               security list, a payment provider's own documentation, a public attack library.
               This is the table that lets a test set I wrote myself survive a hard look."
      >
        <Disclosure summary="Show the full source table" hint="long">
          <Note className="mb-4">
            Rendered from the file in the repository, so there is one copy of this and it
            cannot go stale.
          </Note>
          <Async state={rules} height="14rem">{r => <Markdown text={r.provenance} />}</Async>
        </Disclosure>
      </Panel>
    </ProofPage>
  );
}
