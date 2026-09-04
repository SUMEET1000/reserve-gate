import { useEffect, useRef, useState } from 'react';
import { api, askRows, groupRupees, money, onlyDigits, plainReason } from '../lib/api.js';
import { Button, RazorpayBrand, Roll, SiteFooter, SiteHeader } from '../components/Shell.jsx';
import { Disclosure, ErrorLine, Marginal, Note, Panel, VerdictMark } from '../components/ui.jsx';

// The guided demo, rebuilt into The Orbit Sheet on 2 Sept 2026. It was the one
// page the 1 Sept rebuild did not reach, so until now it carried the old world
// under a new header - and under the night skin its inputs went white on white
// and its route nav collapsed into one unspaced line.
//
// What went, and why it was not restyled. The four checkpoints were a 400vh
// sticky shell driving a 400vw track sideways off vertical scroll. Three things
// were wrong with it at once: a drawing set does not animate, a scroll-derived
// transform fought the browser's own movement, and the whole apparatus existed only
// to show four things that fit on four plates. The plates are what the other six pages
// are drawn with, so the demo is now the same drawing rather than an exception
// inside it. Every call it makes is unchanged.

const STAGES = 4;

// Both label sets are the page's own words and both are kept. They do different
// jobs: the long ones say what you are about to do and sit in the title block,
// the short ones are the station index you jump with.
const CHECKPOINTS = [
  ['Set the limits', 'Limits'],
  ['Ask a real AI model', 'Ask the AI'],
  ['Read each decision', 'Decisions'],
  ['Prove the payment', 'Payment'],
];

// The box opens on the refusal, because that is the beat a panel is here for: a
// real model proposing a purchase that is over the ceiling, and being stopped by
// a rule with a name. The two chips are the other two verdicts, so three
// questions - which is all a visitor gets - reach ALLOW, HOLD and BLOCK.
//
// Every one of them states paise beside the rupees. The tool's own docstring says
// the amount is paise, but a small model reading "7,800 rupees" can still emit
// 7800, which is 78 rupees, which the gate correctly ALLOWS - a demo that proves
// nothing. The committed recording's question says "780000 paise" for the same
// reason, and it is the wording measured to work.
const PREFILL = 'Buy a television for ₹7,800 (780000 paise). Use the create_order tool.';

// Hedged, because the verdict depends on the limits the visitor set in the
// checkpoint above and those are theirs to change. Lower the budget and "should
// pass" becomes a BLOCK, and the label is then simply wrong about what happened.
// "Likely" is the convention the fixed basket beneath already uses.
const EXAMPLES = [
  ['A small buy, likely allowed',
   'Buy a desk lamp for ₹1,200 (120000 paise). Use the create_order tool.'],
  ['A mid-size buy, likely held for approval',
   'Buy a keyboard for ₹3,500 (350000 paise). Use the create_order tool.'],
];

const PREVIEW = [
  ['Headphones', '₹1,800', 'Likely allowed', true],
  ['Monitor arm', '₹2,000', 'Likely allowed', true],
  ['Second monitor arm', '₹2,000', 'Depends on budget left', false],
];

const TECHNICAL = [
  ['/attack', 'Try to break it', 'Send purchases it should refuse'],
  ['/mutate', 'Remove a rule', 'See how much gets through without it'],
  ['/trace', 'Follow one purchase', 'Every step, start to finish'],
  ['/rules', 'Read the rules', 'All nine, in plain words'],
  ['/evidence', 'Check the proof', 'The numbers, and how to re-run them'],
];

const OUTCOME_LABEL = { ALLOW: 'Allowed', HOLD: 'Ask you', BLOCK: 'Blocked' };

// The verdict comes from the deterministic gate, not from model text, so this is
// belt and braces - but the value is spliced into a class name and used as an
// object key, and this whole project exists because adversarial input reaches
// exactly that kind of unvalidated string.
const outcomeOf = row => (row && OUTCOME_LABEL[row.outcome] ? row.outcome : null);

// Razorpay's Checkout script is the one thing on this site fetched from another
// origin, and only after a visitor asks for the ₹100 test payment.
function checkoutScript() {
  return new Promise((resolve, reject) => {
    if (window.Razorpay) return resolve();
    const s = document.createElement('script');
    s.src = 'https://checkout.razorpay.com/v1/checkout.js';
    s.onload = resolve;
    s.onerror = () => reject(new Error('Razorpay Checkout could not be loaded'));
    document.head.append(s);
  });
}

// Which checkpoint the reader is in, for the header count and the station index.
// An observer rather than a scroll handler: it reports the plates themselves,
// so it stays right when a plate grows - and six decisions arriving at
// checkpoint 03 grow one by a screen.
function useStation() {
  const [step, setStep] = useState(1);
  useEffect(() => {
    const plates = [...document.querySelectorAll('[data-step]')];
    if (!plates.length) return;
    // The last plate whose top has passed the reading line, not the plate with
    // the largest visible fraction: a short plate is easier to show in full, so
    // ratio reported 03 while the reader was still at the top of 02.
    const io = new IntersectionObserver(() => {
      const line = innerHeight * 0.35;
      let current = plates[0];
      for (const el of plates) {
        if (el.getBoundingClientRect().top <= line) current = el;
      }
      setStep(Number(current.dataset.step));
    }, { threshold: [0, 0.1, 0.35, 0.65, 1] });
    plates.forEach(el => io.observe(el));
    return () => io.disconnect();
  }, []);
  return step;
}

// A money input. The unit has to be on the field itself: these three numbers are
// rupees while every amount the API carries is paise, and a field that does not
// say which is the one place this site could mislead by omission.
function MoneyField({ id, label, hint, value, onChange }) {
  const hintId = hint ? `${id}-hint` : undefined;
  return (
    <div className="field">
      <label className="field__label" htmlFor={id}>{label}</label>
      {hint && <span id={hintId} className="field__hint">{hint}</span>}
      <span className="field__money">
        <span aria-hidden="true">₹</span>
        {/* type="text", not "number": a number input refuses to render a thousands
            separator, so grouping and type="number" cannot both exist. What is stored is
            still bare digits, so applyLimits is unchanged and no comma reaches the API.
            The pattern replaces the min="1" this used to carry. */}
        <input id={id} type="text" inputMode="numeric" pattern="[1-9][0-9,]*" required
               aria-describedby={hintId}
               value={groupRupees(value)}
               onChange={e => onChange(onlyDigits(e.target.value))} />
      </span>
    </div>
  );
}

export default function Demo() {
  const step = useStation();

  const [limits, setLimits] = useState({ reserved: '10000', max_txn: '5000', approval_over: '2000' });
  const [limitState, setLimitState] = useState({ text: '' });
  const [applying, setApplying] = useState(false);
  const [shopState, setShopState] = useState({ text: '' });
  const [shopping, setShopping] = useState(false);
  const [results, setResults] = useState(null);
  const [question, setQuestion] = useState(PREFILL);
  const [asking, setAsking] = useState(false);
  const [askState, setAskState] = useState({ text: '' });
  // The last reply, for the status line and the four beats, and the rows it
  // produced, kept apart so a second question adds to the decision list instead
  // of replacing what the first one proved.
  const [askData, setAskData] = useState(null);
  const [aiRows, setAiRows] = useState([]);
  // The model call runs for up to 30s. Leaving the page inside that window used
  // to land four setState calls on an unmounted component. It does not give the
  // visitor their question back - the server spent it the moment it was asked -
  // but it stops the page writing to state that no longer exists.
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);
  const [liveState, setLiveState] = useState({
    text: 'Use card number 4100 2800 0000 1007, any expiry date in the future, and any three digits for the CVV.',
  });
  const [liveBusy, setLiveBusy] = useState(false);

  const go = n => document.getElementById(`step-${n}`)?.scrollIntoView({
    behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
    block: 'start',
  });

  function validateLimits(l) {
    const res = Number(l.reserved || 0);
    const max = Number(l.max_txn || 0);
    const app = Number(l.approval_over || 0);
    if (res <= 0) return 'Total budget must be greater than ₹0';
    if (max <= 0) return 'Maximum transaction must be greater than ₹0';
    if (max > res) return 'Maximum transaction cannot exceed total budget';
    if (app >= max) return 'Human approval threshold must be lower than maximum transaction';
    return null;
  }

  async function applyLimits(e) {
    e.preventDefault();
    if (applying) return;
    const validationErr = validateLimits(limits);
    if (validationErr) {
      setLimitState({ text: validationErr, error: true });
      return;
    }
    setApplying(true);
    setLimitState({ text: 'Applying your limits…' });
    try {
      await api('/api/session/reset', {
        reserved: Number(limits.reserved) * 100,
        max_txn: Number(limits.max_txn) * 100,
        approval_over: Number(limits.approval_over) * 100,
      });
      setLimitState({ text: 'Limits applied. Your route is ready.' });
      go(2);
    } catch (err) {
      setLimitState({ text: err.message, error: true });
    } finally {
      setApplying(false);
    }
  }

  async function runAsk(e) {
    e.preventDefault();
    if (asking || !question.trim()) return;
    setAsking(true);
    // Not "Asking the model" - the button already says that, an inch away. The
    // request is given 30 seconds because a tool round trip routinely runs past
    // ten, and nothing on screen used to say so; an unchanging button for fifteen
    // seconds reads as stuck.
    setAskState({ text: 'This can take up to 30 seconds — the model proposes a tool'
                        + ' call, then the gate decides.' });
    try {
      // 30s, not the 10s default: a model call with a tool round-trip in it
      // routinely runs past ten, and the default abort would report a working
      // live call to a judge as a timeout.
      const data = await api('/api/ask', { question }, { timeoutMs: 30000 });
      if (!alive.current) return;
      // A 200 carrying a body that is not an object would otherwise throw on
      // `data.live` two lines down, and the raw TypeError would be shown to the
      // visitor as the status line.
      if (!data || typeof data !== 'object') {
        throw new Error('The server sent something this page could not read.');
      }
      setAskData(data);
      // Live answers only. A recorded reply is a real captured session but it is
      // not this one, and the list below is this visitor's decisions against this
      // visitor's budget. Appending it unconditionally meant a fourth, fifth and
      // sixth click each added the same recorded verdict again and the tally
      // counted them - N decisions claimed where none had happened. The recorded
      // verdict is still shown, in the sequence above, labelled as recorded.
      if (data.live === true) {
        // rows.length inside the updater, not aiRows.length outside it: the
        // latter is the value captured when this handler was created.
        setAiRows(rows => [...rows, ...askRows(data, rows.length)]);
      }
      setAskState({
        text: data.live === true
          ? 'The model answered, and the gate decided.'
          : 'No live model right now, so this is the recorded run below.',
      });
      // Deliberately no go(3) here, unlike the two buttons around it. The answer
      // renders directly below this form, and scrolling a reader away from the
      // thing they just asked for is the one move this panel must not make. The
      // same decision is waiting in checkpoint 03 when they get there.
    } catch (err) {
      if (alive.current) {
        setAskState({ text: `${err.message} — try again in a moment.`, error: true });
      }
    } finally {
      if (alive.current) setAsking(false);
    }
  }

  async function runShop() {
    setShopping(true);
    setShopState({ text: 'Sending six fixed purchase requests…' });
    try {
      const data = await api('/api/shop', {});
      setResults(data.results);
      setShopState({ text: 'Six decisions complete.' });
      go(3);
    } catch (err) {
      setShopState({ text: err.message, error: true });
    } finally {
      setShopping(false);
    }
  }

  // A hold can now come from either list, so the patch goes to both rather than
  // the caller having to know which one it came from. A call id is a server-side
  // reservation id, so it can only ever match in one of them.
  function patchRow(callId, fields) {
    const apply = r => (r.call_id === callId ? { ...r, ...fields } : r);
    setAiRows(rs => rs.map(apply));
    setResults(rs => (rs ? rs.map(apply) : rs));
  }

  async function approve(callId) {
    patchRow(callId, { approving: true, approveError: null });
    try {
      await api('/api/approve', { call_id: callId });
      patchRow(callId, { approved: 'Approved', approving: false, approveError: null });
    } catch (err) {
      patchRow(callId, { approving: false, approveError: err.message });
    }
  }

  async function openCheckout() {
    setLiveBusy(true);
    setLiveState({ text: 'Creating the fixed ₹100 test order…' });
    try {
      const order = await api('/api/live-checkout/order', {});
      await checkoutScript();
      new window.Razorpay({
        key: order.key_id,
        order_id: order.order_id,
        amount: order.amount,
        currency: order.currency,
        name: 'reserve-gate',
        description: order.display_item,
        handler: async response => {
          setLiveState({ text: 'Test payment accepted. Taking the ₹100 through the gate…' });
          try {
            const done = await api('/api/live-checkout/capture',
                                   { payment_id: response.razorpay_payment_id });
            setLiveState({
              text: done.captured
                ? 'Done. The test payment was captured and ₹100 came off your demo balance.'
                : 'The charge did not go through.',
            });
          } catch (err) {
            setLiveState({ text: err.message + ' Recorded proof is still available below.', error: true });
          }
        },
        modal: {
          ondismiss: () => setLiveState({ text: 'Checkout closed. Your sandbox results are unchanged.' }),
        },
      }).open();
    } catch (err) {
      setLiveState({ text: err.message + '. Use the recorded proof link below.', error: true });
    } finally {
      setLiveBusy(false);
    }
  }

  const State = ({ value }) => value.text
    ? (value.error
      ? <ErrorLine>{value.text}</ErrorLine>
      : <p className="demo-said" aria-live="polite">{value.text}</p>)
    : null;

  // What the last reply proposed, and what the gate made of it. Both are derived
  // rather than stored: a second copy of the reply is a second thing that can
  // disagree with it.
  const aiTurns = askData && Array.isArray(askData.turns) ? askData.turns : [];
  const askedRows = askRows(askData);
  // The three-question limit is the server's and is not mirrored here. Disabling
  // the button on `questions_left === 0` was tried and removed: the server answers
  // a fourth question with the recorded run, labelled, and a disabled button hides
  // that - a judge who uses their three questions would get a dead control instead
  // of the fallback the page is meant to show. Measured 4 Sept 2026: the browser
  // pass could not reach the RECORDED FALLBACK state at all until this came out.
  const spent = Boolean(askData && askData.live === true && askData.questions_left === 0);

  // The AI's decisions lead, because they are what the page is now for; the fixed
  // test's rows follow. Both are real decisions from the same ledger, so the
  // tally counts them together.
  const decisions = [...aiRows, ...(results || [])];
  const allowed = decisions.filter(r => r.outcome === 'ALLOW');
  const tally = decisions.length ? {
    count: allowed.length,
    paise: allowed.reduce((sum, r) => sum + (Number(r.paise) || 0), 0),
    refused: decisions.filter(r => r.outcome === 'BLOCK').length,
  } : null;

  return (
    <>
      <SiteHeader>
        <nav aria-label="Main navigation" className="site-nav">
          <span aria-live="polite" className="demo-count">
            {String(step).padStart(2, '0')} / 0{STAGES}
          </span>
          <a href="#technical" className="nav-link max-sm:hidden"><Roll>{'Technical proof ↓'}</Roll></a>
        </nav>
      </SiteHeader>

      <section aria-labelledby="demo-title" className="title-block demo-title-block">
        {/* The same registration ticks the five detail sheets carry, so a
            visitor arriving from any of them is on the same paper. */}
        <span className="title-block__tick" data-at="tl" aria-hidden="true" />
        <span className="title-block__tick" data-at="tr" aria-hidden="true" />

        <Marginal>Guided demo · about 2 minutes</Marginal>
        <h1 id="demo-title">Follow purchase requests<br />through the gate.</h1>
        <p className="title-block__lede">
          Scroll the route. Each checkpoint tells you what will happen before you act.
        </p>

        <Button href="#step-1" variant="primary" className="mt-8">Start with your limits ↓</Button>
      </section>

      {/* The station index. It is the sheet index of a drawing set turned on its
          side: the four stations in order, the one being read inked. It replaces
          a tab strip that was product chrome, and it keeps the short labels. */}
      <nav aria-label="Jump to a checkpoint" className="sheet-index demo-stations">
        <ol>
          {CHECKPOINTS.map(([, short], i) => (
            <li key={short} aria-current={step === i + 1 ? 'step' : undefined}>
              <a href={`#step-${i + 1}`}>
                <span className="sheet-index__no" aria-hidden="true">0{i + 1}</span>
                <span className="sheet-index__label">{short}</span>
              </a>
            </li>
          ))}
        </ol>
      </nav>

      <main id="main-content" tabIndex="-1" className="sheet demo-sheet">
        <Panel
          id="step-1" data-step="1"
          mark="01 / Set limits"
          title="You decide how much authority the buyer gets."
          intro="These values are rupees. Applying them creates a fresh demo budget."
        >
          <form onSubmit={applyLimits}>
            <div className="field-row">
              <MoneyField id="reserved" label="Total budget" hint="Everything the AI can spend"
                          value={limits.reserved}
                          onChange={v => setLimits(l => ({ ...l, reserved: v }))} />
              <MoneyField id="max_txn" label="Maximum single purchase" hint="Anything larger is blocked"
                          value={limits.max_txn}
                          onChange={v => setLimits(l => ({ ...l, max_txn: v }))} />
              <MoneyField id="approval_over" label="Ask me above" hint="Larger purchases wait for you"
                          value={limits.approval_over}
                          onChange={v => setLimits(l => ({ ...l, approval_over: v }))} />
            </div>
            <Button variant="primary" type="submit" className="mt-6" disabled={applying}>
              {applying ? 'Applying limits…' : 'Apply my limits →'}
            </Button>
            <State value={limitState} />
          </form>

          <div className="limits-matrix">
            <div className="limits-card is-allow">
              <div className="limits-card__head">
                <span className="tag is-allow">ALLOW</span>
                <span className="limits-card__range">≤ ₹{groupRupees(limits.approval_over || 0)}</span>
              </div>
              <p className="limits-card__rule">Autonomous pass</p>
              <p className="limits-card__desc">Requests under your threshold pass automatically.</p>
            </div>

            <div className="limits-card is-hold">
              <div className="limits-card__head">
                <span className="tag is-hold">HOLD</span>
                <span className="limits-card__range">₹{groupRupees(limits.approval_over || 0)} – ₹{groupRupees(limits.max_txn || 0)}</span>
              </div>
              <p className="limits-card__rule">Human approval</p>
              <p className="limits-card__desc">Mid-tier purchases pause and wait for your 1-click review at checkpoint 03.</p>
            </div>

            <div className="limits-card is-block">
              <div className="limits-card__head">
                <span className="tag is-block">BLOCK</span>
                <span className="limits-card__range">&gt; ₹{groupRupees(limits.max_txn || 0)}</span>
              </div>
              <p className="limits-card__rule">Hard boundary</p>
              <p className="limits-card__desc">Instantly refused. Exceeds transaction ceiling, never touches payment network.</p>
            </div>
          </div>
        </Panel>

        <Panel
          id="step-2" data-step="2"
          mark="02 / Live AI purchase request"
          title="A real AI model asks. The gate answers."
          intro="Write an instruction and a real Gemini model reads it, then proposes a purchase
                 through the one tool it is given. Gemini proposes the tool call; the deterministic
                 server-side gate decides; this panel never contacts a payment network. If no live
                 model is available right now you will get a recorded example run instead, and it
                 will say so."
        >
          <form onSubmit={runAsk}>
            <div className="field">
              <label className="field__label" htmlFor="ask">Tell the model what to buy</label>
              <span id="ask-hint" className="field__hint">
                The model gets one tool, <code>create_order</code>, and the budget you set
                above.
              </span>
              <textarea id="ask" rows={2} required maxLength={500}
                        aria-describedby="ask-hint"
                        value={question} disabled={asking}
                        onChange={e => setQuestion(e.target.value)} />
            </div>

            <div className="ask-picks">
              {EXAMPLES.map(([label, text]) => (
                <Button key={label} type="button" roll={false} className="ask-pick"
                        aria-label={`Fill the instruction with an example: ${label}`}
                        disabled={asking} onClick={() => setQuestion(text)}>
                  {label}
                </Button>
              ))}
            </div>

            <Button variant="primary" type="submit" className="mt-4"
                    disabled={asking || !question.trim()}>
              {asking ? 'Asking the model…' : 'Send it to the model →'}
            </Button>
            <State value={askState} />
            {spent && (
              <Note className="mt-3">
                That was your third live question. Asking again is not refused, but the
                answer will be the recorded run below, and it will say so.
              </Note>
            )}
          </form>

          {/* The region is rendered whether or not there is an answer in it. An
              aria-live region created in the same tick as its content is not
              reliably announced, so a screen-reader user got one short sentence
              and no cue that thirty lines of the panel's whole point had landed
              below it. aria-busy marks the old answer as stale while a new one is
              in flight; .is-stale dims it for everyone else. */}
          <div role="status" aria-live="polite" aria-atomic="false"
               aria-busy={asking || undefined}
               className={askData ? `ask-out${asking ? ' is-stale' : ''}` : undefined}>
          {askData && (
            <>
              {/* The label is read off the reply and never inferred here. A page that
                  decides for itself whether it is live is a page that can be wrong
                  about the one thing it is claiming. */}
              {askData.live === true ? (
                <p className="ask-status">
                  <span className="tag is-allow">LIVE GEMINI</span>
                  <span className="ask-status__fact">model <code>{askData.model}</code></span>
                  {typeof askData.questions_left === 'number' && (
                    <span className="ask-status__fact">
                      {askData.questions_left} question{askData.questions_left === 1 ? '' : 's'} left
                    </span>
                  )}
                </p>
              ) : (
                <>
                  <p className="ask-status">
                    <span className="tag is-notlive">RECORDED FALLBACK — NOT LIVE</span>
                    {askData.model && (
                      <span className="ask-status__fact">recorded from <code>{askData.model}</code></span>
                    )}
                    {askData.captured && (
                      <span className="ask-status__fact">captured {askData.captured}</span>
                    )}
                  </p>
                  <Note>
                    {askData.reason
                      ? `No live model right now — ${askData.reason}. `
                      : 'No live model right now. '}
                    What follows is a real session captured earlier and committed to the repo. It
                    is not an answer to what you just typed.
                  </Note>
                </>
              )}

              <ol className="ask-steps">
                <li>
                  <h3>{askData.live === true ? 'Your instruction' : 'Recorded example prompt'}</h3>
                  <p className="ask-quote">{askData.question}</p>
                </li>

                <li>
                  <h3>What the model asked for</h3>
                  {aiTurns.length === 0
                    ? <Note>The model answered without proposing a purchase, so the gate had
                            nothing to decide.</Note>
                    : (
                      <ul className="ask-calls">
                        {aiTurns.map((turn, i) => (
                          <li key={i}>
                            <code>{turn.tool || 'unknown tool'}</code>
                            <span className="ask-calls__item">{turn.item || '—'}</span>
                            <span className="ask-calls__amount">
                              {money(turn.amount, turn.currency || 'INR')}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                </li>

                <li>
                  <h3>What reserve-gate decided</h3>
                  {askedRows.length === 0
                    ? <Note>Nothing to decide — see above.</Note>
                    : askedRows.map((row, i) => {
                        const outcome = outcomeOf(row);
                        return (
                        <div key={row.key || i} data-outcome={outcome || 'none'}
                             className={`verdict-row is-${(outcome || 'none').toLowerCase()}`}>
                          <VerdictMark outcome={outcome} />
                          <div className="verdict-row__body">
                            <div className="verdict-row__head">
                              <span className="verdict-row__tag">
                                {outcome ? OUTCOME_LABEL[outcome] : 'no verdict'}
                              </span>
                              {row.rule && <code>{row.rule}</code>}
                              <span className="verdict-row__title">{row.name}</span>
                              <span className="verdict-row__amount">
                                {money(row.paise, row.currency)}
                              </span>
                            </div>
                            <div className="verdict-row__why">
                              {plainReason(row, row.paise, row.currency)}
                            </div>
                            {outcome === 'HOLD' && row.call_id && (
                              <p className="verdict-row__from">
                                Waiting for your approval — release it in checkpoint 03 below.
                              </p>
                            )}
                          </div>
                        </div>
                        );
                      })}
                </li>

                <li>
                  <h3>What the model said back</h3>
                  <p className="ask-quote">{askData.answer || '—'}</p>
                </li>
              </ol>

              <Note className="mt-4">
                The model proposed. It could not approve, raise a limit, or reach Razorpay: the
                verdict above came from the same <code>ledger.authorize()</code> the MCP tools run
                on, live or recorded, and every verdict shown here is in the audit log.
              </Note>
            </>
          )}
          </div>

          <Disclosure className="mt-8"
                      summary="Or run the repeatable fixed test"
                      hint="no AI, no model key">
            <Note>
              This sends the same six requests every time, so the result is identical on every
              machine. No AI model chooses these items, and it needs no model key — it is the
              reproducible check to run when the live box above is unavailable.
            </Note>
            <ul className="basket mt-4">
              {PREVIEW.map(([name, price, verdict, good]) => (
                <li key={name}>
                  <span className="basket__name">{name}</span>
                  <span className="basket__price">{price}</span>
                  <span className={`basket__call${good ? ' is-likely' : ''}`}>{verdict}</span>
                </li>
              ))}
            </ul>
            <Button className="mt-6" onClick={runShop} disabled={shopping}>
              Run the fixed shopping test →
            </Button>
            <State value={shopState} />
          </Disclosure>
        </Panel>

        <Panel
          id="step-3" data-step="3"
          mark="03 / Decisions"
          title="Every result says what happened and why."
          intro={<>
            Every decision the AI asked for, and every decision the fixed test asked for, in one
            list. <strong>Allowed</strong> is reserved against your demo budget.{' '}
            <strong>Ask you</strong> is set aside and waiting for your approval.{' '}
            <strong>Blocked</strong> never reaches a payment network.
          </>}
        >
          {tally && (
            <div className="reading is-allow">
              <h3>Allowed request volume</h3>
              <p className="reading__figure">
                {tally.count} {tally.count === 1 ? 'request' : 'requests'} · {money(tally.paise)}
              </p>
              <Note className="mt-3">
                {tally.count} {tally.count === 1 ? 'request was' : 'requests were'} allowed and
                reserved against your demo budget. No payment is created on this path.
                {tally.refused > 0 && ` ${tally.refused} refused, and a refusal never
                reaches Razorpay.`}
                {decisions.some(r => r.source === 'ai' && r.live === false)
                  && ' One or more rows below came from the recorded example rather than a live'
                   + ' model, and were not decided against your session.'}
              </Note>
            </div>
          )}

          {decisions.map((r, index) => (
            <div key={r.key || r.call_id || `${r.source || 'fixed'}-${r.name}-${index}`}
                 data-outcome={outcomeOf(r) || 'none'} data-source={r.source || 'fixed'} data-reveal
                 style={{ animationDelay: `${index * 140}ms` }}
                 className={`verdict-row reveal reveal-quick is-${(outcomeOf(r) || 'none').toLowerCase()} ${r.approved ? 'is-settled' : ''}`}>
              <VerdictMark outcome={outcomeOf(r)} />
              <div className="verdict-row__body">
                <div className="verdict-row__head">
                  <span className="verdict-row__tag">{outcomeOf(r) ? OUTCOME_LABEL[r.outcome] : 'no verdict'}</span>
                  {r.rule && <code>{r.rule}</code>}
                  <span className="verdict-row__title">{r.name}</span>
                  <span className="verdict-row__amount">{money(r.paise, r.currency || 'INR')}</span>
                </div>
                {/* Which of the two asked for this, and - for the AI's - whether it
                    was a live call or the recorded example. A reader whose whole job
                    is telling those apart cannot do it from a row that says only
                    "asked for by the AI model". */}
                <p className="verdict-row__from">
                  {r.source !== 'ai'
                    ? 'From the fixed test'
                    : r.live
                      ? 'Asked for by the AI model, live'
                      : 'Asked for by the AI model — recorded example, not live'}
                </p>
                <details className="verdict-row__fold">
                  <summary aria-label={`Why was ${r.name} ${(r.outcome || '').toLowerCase()}?`}>Why?</summary>
                  <p>{plainReason(r, r.paise, r.currency || 'INR')}{r.rule ? ` · Rule ${r.rule}` : ''}</p>
                </details>
                {r.call_id && (r.approved
                  ? <p className="verdict-row__approved-flash">{r.approved}</p>
                  : <div className="mt-3 flex flex-wrap items-center gap-3">
                      <Button onClick={() => approve(r.call_id)} disabled={r.approving}>
                        {r.approving ? 'Approving…' : 'Approve'}
                      </Button>
                      {r.approveError && <span className="error-line text-xs">{r.approveError}</span>}
                    </div>)}
              </div>
            </div>
          ))}

          {!results && (
            <div className="decisions-pending">
              <div className="decisions-pending__intro">
                <div>
                  <p className="decisions-pending__title">Fixed shopping test awaiting evaluation</p>
                  <Note>Open <em>Or run the repeatable fixed test</em> above to send these
                        six requests against your limits.</Note>
                </div>
              </div>

              <div className="decisions-queue">
                {PREVIEW.map(([name, price, verdict, good]) => (
                  <div key={name} className="queue-row">
                    <div className="queue-row__item">
                      <span className="queue-row__status tag">QUEUED</span>
                      <strong className="queue-row__name">{name}</strong>
                    </div>
                    <span className="queue-row__price">{price}</span>
                    <span className={`queue-row__expected tag ${good ? 'is-allow' : 'is-hold'}`}>{verdict}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </Panel>

        <Panel
          id="step-4" data-step="4"
          mark="04 / Test payment"
          title="Complete a real Razorpay test-mode checkout."
          intro="This fixed ₹100 verification purchase uses Razorpay's real Checkout interface
                 with a test card. No real money moves, and no AI chooses this purchase. After
                 capture, your demo balance drops by ₹100 and the event is written to the audit
                 log. If Checkout will not open, a recorded run is linked below."
        >
          <div className="reading checkout-box">
            <div className="checkout-box__header">
              <p className="test-stamp">TEST MODE · NO REAL MONEY</p>
              <RazorpayBrand>Secured test checkout by <strong>Razorpay</strong></RazorpayBrand>
            </div>
            <p className="checkout-item">reserve-gate verification purchase</p>
            <div className="checkout-action-row">
              <p className="checkout-price">₹100</p>
              <Button variant="primary" onClick={openCheckout} disabled={liveBusy}>
                Pay ₹100 with a test card
              </Button>
            </div>
            <State value={liveState} />
            <p className="checkout-alt"><a href="/trace">Or watch a recorded one, step by step</a></p>
          </div>
        </Panel>

        <Panel
          id="technical"
          mark="Optional / for the curious"
          title="Don't take our word for it."
          intro="Each page exposes either a live check or a committed artifact and labels which
                 one you are seeing."
        >
          <ul className="onward">
            {TECHNICAL.map(([href, title, hint]) => (
              <li key={href}>
                <a href={href}>
                  <span className="onward__title">{title}</span>
                  <span className="onward__hint">{hint}</span>
                </a>
              </li>
            ))}
          </ul>
        </Panel>
      </main>

      <SiteFooter>
        <a href="/">← Landing page</a>
        <span>Sandbox remains usable without Razorpay credentials.</span>
      </SiteFooter>
    </>
  );
}
