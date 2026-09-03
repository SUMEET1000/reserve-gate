import { Brand, Button, Roll, SiteFooter, SiteHeader } from '../components/Shell.jsx';
import HeroScene from '../components/HeroScene.jsx';

// The Orbit Sheet. Every section is a plate on one drawing: a numbered rule
// across the top, the content set against it, and measurement in the margin.
// Nothing here survives from the previous world - the route strip, the three
// verdict solids and the closing poster were all cut on 1 Sept 2026. The words
// are unchanged; only what draws them is new.

const BEATS = [
  ['A', 'You set the limits', 'Define budgets, rules, and approvals in one place.'],
  ['B', 'The AI proposes', 'The AI finds what is needed and submits a purchase request.'],
  ['C', 'The gate decides', 'Every request is checked, held, or blocked before any money moves.'],
];

const OUTCOMES = [
  { key: 'ALLOW', tone: 'text-allow', mark: 'through',
    body: 'It fits every rule. The money moves and the balance goes down.' },
  { key: 'HOLD', tone: 'text-hold', mark: 'held',
    body: 'It is bigger than the amount you said to ask about. It waits for your yes.' },
  { key: 'BLOCK', tone: 'text-block', mark: 'stopped',
    body: 'It breaks a rule. Nothing is sent, nothing is charged, and the reason is named.' },
];

// The three verdicts, drawn the way a drawing distinguishes states: one gate
// outline, one line of travel, and where that line stops. No colour does the
// work alone, so the three read apart in print, in greyscale and to anyone who
// cannot separate red from green.
// Each mark gets a box cut to its own ink, because the three do not draw to the
// same width: ALLOW runs to x=196 (line, gate, arrow), HOLD stops at 172 and
// BLOCK at 126. Sharing one 0..200 box left-anchored all three, so centring the
// box left the *drawing* off-centre - measured 1 Sept 2026 at 1920: ALLOW 0 px,
// HOLD -24.2 px, BLOCK -70.7 px against its column's middle. The y range is
// identical for all three, so they still share one height and one stroke scale.
const PLATE_BOX = {
  through: '3 21 194 92',
  held: '3 21 170 92',
  stopped: '3 21 124 92',
};

function VerdictPlate({ mark }) {
  const line = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.1 };
  return (
    <svg viewBox={PLATE_BOX[mark]} aria-hidden="true" className="verdict-plate">
      <path d="M74 22h12v76H74zM114 22h12v76h-12zM74 22h52v12H74z" {...line} />
      <path d="M4 60h66" {...line} strokeDasharray={mark === 'stopped' ? '0' : '0'} />
      {mark === 'through' && <>
        <path d="M130 60h66" {...line} />
        <path d="m188 54 8 6-8 6" {...line} />
      </>}
      {mark === 'held' && <>
        <path d="M130 60h30" {...line} strokeDasharray="4 5" />
        <path d="M164 44v32" {...line} strokeWidth="2.4" />
        <path d="M172 44v32" {...line} strokeWidth="2.4" />
      </>}
      {mark === 'stopped' && (
        <path d="m56 46 28 28M84 46 56 74" {...line} strokeWidth="2.4" />
      )}
      <circle cx="100" cy="110" r="2" fill="currentColor" />
    </svg>
  );
}

// One plate heading, built on the beats' grid: the key letter in the margin, the
// title, and the lede as its own column beside it, then the rule across. The
// lede used to sit under the rule at the far left, which made a keyed plate read
// as a different kind of row from A, B and C.
function PlateHead({ mark, title, id, lede }) {
  return (
    // The last plate carries no key letter - a drawing keys the details it
    // cross-references, and nothing refers back to this one. Without a key there
    // is no margin column to hold, so its title starts at the sheet edge rather
    // than indented against an empty track.
    <div className={`plate-head${mark ? '' : ' plate-head--open'}`}>
      {mark && <span className="plate-mark" aria-hidden="true">{mark}</span>}
      <h2 id={id}>{title}</h2>
      {lede && <p className="plate-lede">{lede}</p>}
    </div>
  );
}

export default function Landing() {
  return (
    <>
      <SiteHeader>
        <nav aria-label="Main navigation" className="flex items-center gap-6 sm:gap-10">
          <a href="#how" className="nav-link max-sm:hidden"><Roll>How it works</Roll></a>
          <a href="#proof" className="nav-link max-sm:hidden"><Roll>Proof</Roll></a>
          <Button href="/demo" className="max-sm:px-3">Try the guided demo</Button>
        </nav>
      </SiteHeader>

      <main>
        <section aria-labelledby="hero-title" className="landing-hero relative overflow-hidden">
          {/* One canvas for the whole section. The rings are the page ground
              here, not a panel beside the text, which is the only way the gate
              can be seen to bend the same lines the sheet is drawn with. */}
          <HeroScene className="absolute inset-0 h-full w-full" />

          <svg
            aria-hidden="true"
            className="hero-dimensions"
            viewBox="0 0 1000 700"
          >
            <g>
              <line x1="600" y1="130" x2="820" y2="130" />
              <line x1="600" y1="130" x2="600" y2="202" />
              <line x1="820" y1="130" x2="820" y2="202" />
              <path d="M600 130l10-5v10zM820 130l-10-5v10z" />
              <text x="710" y="116" textAnchor="middle">3.16 W</text>

              <line x1="858" y1="190" x2="858" y2="520" />
              <line x1="792" y1="190" x2="858" y2="190" />
              <line x1="792" y1="520" x2="858" y2="520" />
              <path d="M858 190l-5 10h10zM858 520l-5-10h10z" />
              <text x="875" y="355" textAnchor="middle" transform="rotate(90 875 355)">4.84 H</text>

              <line x1="625" y1="570" x2="815" y2="570" />
              <line x1="625" y1="520" x2="625" y2="570" />
              <line x1="815" y1="520" x2="815" y2="570" />
              <path d="M625 570l10-5v10zM815 570l-10-5v10z" />
              <text x="720" y="592" textAnchor="middle">1.55 D</text>
            </g>
          </svg>

          {/* Sheet edge notes. These four words were a chip row lower down the
              page; on a drawing they belong in the margins. Words unchanged. */}
          <span className="sheet-note" data-at="tl">Real policy</span>
          <span className="sheet-note" data-at="tr">Real ledger</span>
          <span className="sheet-note" data-at="bl">Razorpay test mode</span>
          <span className="sheet-note" data-at="br">Hash-chained audit</span>

          {/* The ghost layer, and it is the page's own words rather than filler:
              a specification printed under its drawing says the same thing
              twice, which is the point. Inert to pointer, selection and
              assistive tech, because it is texture and not text. */}
          <p aria-hidden="true" className="ghost-text max-lg:hidden">
            RG Routing Manifest → every money call is checked against the block before
            it is forwarded. R0 currency and unit · R1 block cap · R2 expiry ·
            R3 multiple debits · R4 revocation · R5 per-transaction cap ·
            R6 velocity window · R7 idempotency. Decision, rule, amount attempted and
            remaining balance are written to one append-only hash-chained record.
          </p>

          <div className="hero-grid">
            <div className="hero-copy">
              <h1 id="hero-title" className="hero-title settle">
                AI spends.<br />You set the ceiling.
              </h1>
              {/* Cut to two lines on 2 Sept 2026 on Sumeet's instruction: the long
                  version ran under the gate's column at 1366 and below. The claim
                  is unchanged - a prompt asks, a balance decides. */}
              <p className="hero-lede settle" style={{ '--settle-delay': '.12s' }}>
                A limit in a prompt is a request. reserve-gate makes it a balance,
                checked on every purchase.
              </p>
              <div className="settle mt-10" style={{ '--settle-delay': '.24s' }}>
                <Button href="/demo" variant="primary">
                  Try the guided demo <span aria-hidden="true">→</span>
                </Button>
              </div>
            </div>
            {/* The gate is drawn by the canvas behind this grid; the cell is the
                hole left for it, so the copy never lands on top of it. */}
            <div aria-hidden="true" className="hero-void" />
          </div>
        </section>

        {/* Three plates, one per beat, each given a full band of the sheet. They
            were three small bordered cards in a single row; at that size the
            only thing a visitor read was the icon. */}
        <section id="how" aria-label="How reserve-gate works" className="beats">
          {/* The key letter carries the sequence; a second `01 / 03` beside it
              numbered the same three things twice and was the only thing sitting
              in the band's empty right half. Cut 1 Sept 2026 with the widening. */}
          {BEATS.map(([mark, title, body]) => (
            <article key={title} data-reveal className="reveal beat">
              <span className="beat-mark" aria-hidden="true">{mark}</span>
              <div className="beat-body">
                <h2>{title}</h2>
                <p>{body}</p>
              </div>
            </article>
          ))}
        </section>

        {/* The three verdicts are the vocabulary every other page uses, so this
            plate is given the whole viewport and its content sits centred in it
            rather than stacked at the top of a short band. */}
        <section id="outcomes" aria-labelledby="outcomes-title" className="plate plate--full">
          <PlateHead
            mark="D"
            id="outcomes-title"
            title="Three answers. Nothing else."
            lede="Every purchase an AI proposes comes back as one of these, with the reason attached. There is no fourth answer and no way to argue with the one you get."
          />

          <dl className="verdicts">
            {OUTCOMES.map(o => (
              <div key={o.key} data-reveal className="reveal verdict">
                <span className={o.tone}><VerdictPlate mark={o.mark} /></span>
                <dt className={`verdict-key ${o.tone}`}>{o.key}</dt>
                <dd>{o.body}</dd>
              </div>
            ))}
          </dl>
        </section>

        <section id="proof" aria-labelledby="proof-title" className="plate">
          <PlateHead
            id="proof-title"
            title="Does it actually stop anything?"
            lede="The gate holds sixteen separate guards. We deleted all sixteen and replayed the same 150 attack cases through it."
          />

          {/* Two readings of one measurement, set as a drawing sets a before and
              an after: the same two quantities in the same two columns, so the
              only thing that changes between them is the number. */}
          <div className="gate-off">
            {[
              { tone: 'block', head: 'All sixteen guards deleted', calls: '37', money: '₹68,502' },
              { tone: 'allow', head: 'All sixteen guards on', calls: '0', money: '₹0' },
            ].map(r => (
              <div key={r.head} data-reveal className={`reveal reading is-${r.tone}`}>
                <h3>{r.head}</h3>
                <dl>
                  <div>
                    <dd>{r.calls}</dd>
                    <dt>money calls got through</dt>
                  </div>
                  <div>
                    <dd>{r.money}</dd>
                    <dt>of real money moved</dt>
                  </div>
                </dl>
              </div>
            ))}
          </div>

          <p className="plate-foot">
            Measured 1 September 2026 by <code>harness/gate_off.py</code>,
            which exits non-zero if the second row is ever dirty — so it cannot report a win by
            being broken. Of the 150 cases, 130 try to move money and 80 of those should be
            refused. The 43 that never got through even with every guard deleted were caught by
            code those deletions do not cover; the run names them rather than claiming credit.
          </p>
        </section>

        {/* The close. It carried a dark gradient poster from the old world; what
            it needs is the sentence and one way in, on the same sheet as
            everything above it. */}
        <section className="landing-close" aria-labelledby="close-title">
          <h2 id="close-title">The gate stays between intent and payment.</h2>
          <p>
            The AI can prepare the request. It cannot raise its own limit, approve itself,
            or skip the record that explains what happened.
          </p>
          <Button href="/demo" variant="primary">
            Follow a purchase <span aria-hidden="true">→</span>
          </Button>
          <p className="close-note">
            The AI proposes. Your limits decide. The record stays behind.
          </p>
        </section>
      </main>

      <SiteFooter>
        <Brand className="text-base" />
        <a href="/attack">Try to break it</a>
        <a href="/evidence">The proof</a>
      </SiteFooter>
    </>
  );
}
