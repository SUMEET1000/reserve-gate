import { useEffect, useState } from 'react';
import { api, groupRupees, money, onlyDigits, plainReason } from '../lib/api.js';
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
  ['Run the plan', 'Plan'],
  ['Read each decision', 'Decisions'],
  ['Prove the payment', 'Payment'],
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

  async function approve(callId) {
    setResults(rs => rs.map(r => r.call_id === callId ? { ...r, approving: true, approveError: null } : r));
    try {
      await api('/api/approve', { call_id: callId });
      setResults(rs => rs.map(r => r.call_id === callId
        ? { ...r, approved: 'Approved', approving: false, approveError: null } : r));
    } catch (err) {
      setResults(rs => rs.map(r => r.call_id === callId
        ? { ...r, approving: false, approveError: err.message } : r));
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

  const allowed = results ? results.filter(r => r.outcome === 'ALLOW') : [];
  const tally = results && {
    count: allowed.length,
    paise: allowed.reduce((sum, r) => sum + r.paise, 0),
    refused: results.filter(r => r.outcome === 'BLOCK').length,
  };

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
          mark="02 / Preview"
          title="A fixed basket tests the gate."
          intro="This deterministic walkthrough sends the same six requests every time. No AI
                 model chooses these items. Small requests pass, larger ones wait for approval,
                 and anything above the purchase limit is blocked."
        >
          <ul className="basket">
            {PREVIEW.map(([name, price, verdict, good]) => (
              <li key={name}>
                <span className="basket__name">{name}</span>
                <span className="basket__price">{price}</span>
                <span className={`basket__call${good ? ' is-likely' : ''}`}>{verdict}</span>
              </li>
            ))}
          </ul>
          <Button variant="primary" className="mt-6" onClick={runShop} disabled={shopping}>
            Run the fixed shopping test →
          </Button>
          <State value={shopState} />
        </Panel>

        <Panel
          id="step-3" data-step="3"
          mark="03 / Decisions"
          title="Every result says what happened and why."
          intro={<>
            <strong>Allowed</strong> is reserved against your demo budget. <strong>Ask you</strong>{' '}
            is set aside and waiting for your approval. <strong>Blocked</strong> never reaches
            a payment network.
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
                reserved against your demo budget. No payment is created in this fixed test.
                {tally.refused > 0 && ` ${tally.refused} refused, and a refusal never
                reaches Razorpay.`}
              </Note>
            </div>
          )}

          {!results && (
            <div className="decisions-pending">
              <div className="decisions-pending__intro">
                <div>
                  <p className="decisions-pending__title">Fixed shopping test awaiting evaluation</p>
                  <Note>Run the fixed test in step 02 to evaluate these requests against your limits and see the policy decisions.</Note>
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

          {results && results.map((r, index) => (
            <div key={r.call_id || r.name}
                 data-outcome={r.outcome} data-reveal
                 style={{ animationDelay: `${index * 140}ms` }}
                 className={`verdict-row reveal reveal-quick is-${(r.outcome || '').toLowerCase()} ${r.approved ? 'is-settled' : ''}`}>
              <VerdictMark outcome={r.outcome} />
              <div className="verdict-row__body">
                <div className="verdict-row__head">
                  <span className="verdict-row__tag">{OUTCOME_LABEL[r.outcome] || r.outcome}</span>
                  {r.rule && <code>{r.rule}</code>}
                  <span className="verdict-row__title">{r.name}</span>
                  <span className="verdict-row__amount">{money(r.paise)}</span>
                </div>
                <details className="verdict-row__fold">
                  <summary aria-label={`Why was ${r.name} ${(r.outcome || '').toLowerCase()}?`}>Why?</summary>
                  <p>{plainReason(r, r.paise, 'INR')}{r.rule ? ` · Rule ${r.rule}` : ''}</p>
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
          <Disclosure summary="Want to shop for something else?" className="mt-8">
            <Note>
              This walkthrough sends the same desk-setup requests every time, so the results are
              repeatable. It does not call an AI model. To choose your own request, use the page
              below.
            </Note>
            <p className="mt-3"><a href="/attack">Build a custom request →</a></p>
          </Disclosure>
        </Panel>
      </main>

      <SiteFooter>
        <a href="/">← Landing page</a>
        <span>Sandbox remains usable without Razorpay credentials.</span>
      </SiteFooter>
    </>
  );
}
