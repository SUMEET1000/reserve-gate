import { memo, useEffect, useRef, useState } from 'react';
import { detailValue, md, money, plainReason } from '../lib/api.js';

// The primitives every detail sheet is drawn with. Written for The Orbit Sheet
// rather than adapted to it: the old set was a glass system - white fills, a
// specular streak across every surface, a 6px coloured spine down each decision
// - and none of that is how a drawing shows anything. Here a surface is paper
// with a hairline around it, and difference is carried by form and position
// before it is carried by colour.

// The label of a control, split so each letter can leave on its own beat.
// Measured on the reference 2 Sept 2026 (Playwright, 1440x900): each letter is
// its own inline-block span inside a box clipped to exactly one line height,
// travels y 0 -> -100% on hover while a second copy rides up from +100%, and the
// letters depart left to right about 18 ms apart, settling in roughly half a
// second on a hard deceleration.
//
// The incoming copy is a ::before carrying the same letter, so one span holds
// both halves of the swap and there is no duplicated markup for a screen reader
// to read twice. The split itself is hidden from assistive tech and the real
// word is carried once, unsplit, beside it - letters announced one at a time is
// what an unguarded split does to a screen reader.
const segmenter = typeof Intl !== 'undefined' && Intl.Segmenter
  ? new Intl.Segmenter('en', { granularity: 'grapheme' })
  : null;

function splitGraphemes(text) {
  if (!segmenter) return [...text];
  return Array.from(segmenter.segment(text), s => s.segment);
}

export const Roll = memo(function Roll({ children }) {
  // A label is often a string beside an element - "Follow a purchase" and its
  // arrow. Only the words are split; anything that is not text is passed through
  // and stays put, because a ::before cannot carry a copy of a React element.
  const parts = Array.isArray(children) ? children : [children];
  if (!parts.some(part => typeof part === 'string')) return children;

  // One counter across the whole label, so the wave reads left to right through
  // the words rather than restarting at each part.
  let n = 0;
  return (
    <span className="roll">
      <span className="roll__sr">{children}</span>
      <span className="roll__split" aria-hidden="true">
        {parts.map((part, pi) => (
          typeof part !== 'string' ? <span key={pi} className="roll__still">{part}</span>
            // Each word gets its own inline-block, so a line break can only land
            // between words, never inside one - a bare per-letter span is itself
            // a valid break point, which used to split a wrapped label mid-word
            // (measured on the landing hero CTA at 320px, 3 Sept 2026).
            : part.split(/(\s+)/).filter(Boolean).map((seg, si) => (
              /\s/.test(seg)
                ? splitGraphemes(seg).map((ch, ci) => (
                    <span key={`${pi}-${si}-${ci}`} className="roll__c" data-c={ch} style={{ '--i': n++ }}>{' '}</span>
                  ))
                : (
                  <span key={`${pi}-${si}`} className="roll__word">
                    {splitGraphemes(seg).map((ch, ci) => (
                      <span key={ci} className="roll__c" data-c={ch} style={{ '--i': n++ }}>{ch}</span>
                    ))}
                  </span>
                )
            ))
        ))}
      </span>
    </span>
  );
});

// A link that looks like a button is still a link: navigation belongs to the
// anchor so middle-click, copy-link and the browser's own focus order all work.
export function Button({ variant = 'outline', className = '', href, roll = true, children, ...props }) {
  const cls = `btn btn--${variant} ${className}`;
  // `roll` is opt-out at the call site rather than a check on the class: what
  // decides is the shape of the label, not the variant wearing it. The roll
  // clips to one line, so a label that wraps puts each incoming ::before copy
  // on top of the line below it - garbled at rest, no hover involved. That is
  // what the `.pick` chips on /mutate, /attack and /trace are: whole sentences
  // in a narrow grid cell. Rejected on sight 2 Sept 2026.
  const inner = roll ? <Roll>{children}</Roll> : children;
  if (href) return <a href={href} className={cls} {...props}>{inner}</a>;
  return <button className={cls} {...props}>{inner}</button>;
}

// A key in the margin, the way a drawing keys a detail to its plate. This
// replaces the eyebrow that used to sit above headings on the guided demo:
// an eyebrow is a label wearing a heading's position, and the craft floor bans
// it outright. A key is a different thing - it sits beside the content it
// numbers, and the number is the sequence a visitor is actually walking.
export function Marginal({ children, className = '' }) {
  return <p className={`marginal ${className}`}>{children}</p>;
}

// One plate on the sheet: the rule across the top, a short crimson stub at its
// left end the way a drawing marks where a plate begins, the heading held in
// the margin column, and the content set against it. The landing page's
// sections are the same object, which is what makes the two halves one drawing.
// `mark` keys the plate the way the landing page keys A, B and C - the guided
// demo is a numbered sequence and the number is how a visitor knows where in it
// they are. Extra props reach the section so a page can give a plate an id to
// jump to; the guided demo's station index needs one per checkpoint.
export function Panel({ title, intro, mark, children, className = '', ...rest }) {
  return (
    <section data-reveal className={`plate-row reveal ${className}`} {...rest}>
      <header>
        {mark && <Marginal>{mark}</Marginal>}
        {title && <h2>{title}</h2>}
        {intro && <p className="plate-row__intro">{intro}</p>}
      </header>
      <div className="plate-row__body">{children}</div>
    </section>
  );
}

// A command block, set the way a modern editor sets one: dark panel, numbered
// gutter, and the parts of a command coloured apart. This is the one surface on
// the site that is not cream paper, and it earns the exception the way a code
// listing pasted into a drawing does - it is a different medium, quoted.
//
// Highlighting is done here rather than by a library: the CSP is closed, the
// page has no outbound request in it, and a shell tokeniser small enough to
// read is smaller than the dependency. It handles what these blocks contain -
// shell commands - and nothing it does not recognise is left as plain text
// rather than guessed at.
const SHELL_WORD = /^(git|pip|python|pytest|cd|npx|node|curl|bash|export|ots)$/;

function tokenize(line) {
  const out = [];
  // One pass, longest-first: URLs before paths, flags before words, so a flag
  // carrying a slash is not split into two tokens.
  const re = /(https?:\/\/\S+)|(&&|\|\||[|;])|(--?[A-Za-z][\w-]*)|("[^"]*"|'[^']*')|(\S+)|(\s+)/g;
  let m, first = true;
  while ((m = re.exec(line))) {
    const [text, url, op, flag, str, word, space] = m;
    let kind = 'plain';
    if (url) kind = 'url';
    else if (op) kind = 'op';
    else if (flag) kind = 'flag';
    else if (str) kind = 'str';
    else if (space) kind = 'space';
    else if (word) {
      // The first word of a line or of a piped segment is the command being
      // run; the same word later is an argument and is not coloured as one.
      if (first && SHELL_WORD.test(word)) kind = 'cmd';
      else if (/[/.]/.test(word)) kind = 'path';
    }
    if (!space) first = op ? true : false;
    out.push({ text, kind });
  }
  return out;
}

export function CodeBlock({ code, plain = false }) {
  const lines = String(code).replace(/\n$/, '').split('\n');
  return (
    <div className="code">
      <pre>
        {lines.map((line, i) => (
          <span className="code__line" key={i}>
            {/* The gutter is aria-hidden, and it has to be: a screen reader
                reading "dollar git clone dollar pip install" is worse than no
                gutter at all, and a prompt copied into a shell does not run.
                Commands get the prompt; a quoted data listing gets its line
                number, because nobody types a line of JSON at a prompt. */}
            <span className="code__no" aria-hidden="true">{plain ? i + 1 : '$'}</span>
            <span className="code__text">
              {plain
                ? line
                : tokenize(line).map((t, j) => (
                    <span key={j} className={`t-${t.kind}`}>{t.text}</span>
                  ))}
            </span>
          </span>
        ))}
      </pre>
    </div>
  );
}

function Chevron() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true" className="chev">
      <path d="M5.5 3.5 10.5 8l-5 4.5" fill="none" stroke="currentColor"
            strokeWidth="1.6" strokeLinecap="square" />
    </svg>
  );
}

// Progressive disclosure for the committed documents. Four of them run to
// thousands of pixels; open by default they were the page, and the thing a
// visitor came to do sat above an unreadable wall.
export function Disclosure({ summary, hint, children, className = '' }) {
  return (
    <details className={`fold ${className}`}>
      <summary>
        <Chevron />
        <span>{summary}</span>
        {hint && <span className="fold__hint">{hint}</span>}
      </summary>
      <div className="fold__body">{children}</div>
    </details>
  );
}

export function Note({ children, className = '' }) {
  return <p className={`note ${className}`}>{children}</p>;
}

// A pending measurement, drawn as one: the dashed outline a drawing uses for a
// part that is specified but not yet present.
export function Skeleton({ height = '4rem' }) {
  return <div style={{ height }} aria-hidden="true" className="pending" />;
}

// The three verdicts, drawn the same way the landing page draws them: one gate
// outline, one line of travel, and where that line stops. Form carries the
// difference, so the three still read apart in greyscale and to anyone who
// cannot separate red from green - and the 6px coloured spine this replaced
// was doing that job with colour alone.
const MARK = { ALLOW: 'through', HOLD: 'held', BLOCK: 'stopped' };

export function VerdictMark({ outcome }) {
  const mark = MARK[outcome];
  const line = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.6 };
  return (
    <svg viewBox="0 0 60 44" aria-hidden="true" className="verdict-mark">
      <path d="M22 8h5v28h-5zM33 8h5v28h-5zM22 8h16v5H22z" {...line} />
      <path d="M2 22h18" {...line} />
      {mark === 'through' && <>
        <path d="M40 22h18" {...line} />
        <path d="m53 18 5 4-5 4" {...line} />
      </>}
      {mark === 'held' && <>
        <path d="M40 22h8" {...line} strokeDasharray="3 3" />
        <path d="M51 14v16M55 14v16" {...line} strokeWidth="2.2" />
      </>}
      {mark === 'stopped' && <path d="m11 15 10 14M21 15 11 29" {...line} strokeWidth="2.2" />}
    </svg>
  );
}

// One decision, stamped on the sheet.
export function Verdict({ decision: d, title, children }) {
  const tone = MARK[d.outcome] ? d.outcome.toLowerCase() : 'none';
  const currency = d.detail?.currency || 'INR';
  const detail = Object.entries(d.detail || {})
    .map(([k, v]) => `${k}=${detailValue(k, v, currency)}`)
    .join(' · ');
  return (
    // The outcome is on the element as data so a browser test can select a
    // decision without matching on colour classes or on prose.
    <div data-outcome={d.outcome} data-reveal
         className={`verdict-row reveal reveal-quick is-${tone}`}>
      <VerdictMark outcome={d.outcome} />
      <div className="verdict-row__body">
        <div className="verdict-row__head">
          <span className="verdict-row__tag">{d.outcome}</span>
          {d.rule && <code>{d.rule}</code>}
          {title && <bdi className="verdict-row__title">{title}</bdi>}
        </div>
        {plainReason(d) && <div className="verdict-row__why">{plainReason(d)}</div>}
        {detail && <div className="verdict-row__detail">{detail}</div>}
        {d.call_id && (
          <div className="verdict-row__id">call id <code>{d.call_id}</code></div>
        )}
        {children}
      </div>
    </div>
  );
}

// Spent / held / available as one bar. The three are stacked widths rather than
// three numbers because "how much is left" is the question a visitor actually
// has, and a bar answers it without arithmetic. The bar is a dimension line
// here: hairline ends, the segments hatched rather than filled, so it belongs
// to the drawing instead of sitting on top of it.
export function Meter({ block: b }) {
  if (!b || !b.reserved) return <p className="note">no block yet</p>;
  const pct = n => (Math.min(100, Math.max(0, 100 * n / b.reserved))).toFixed(2) + '%';
  return (
    <div className="meter">
      <div
        role="img"
        aria-label={`${money(b.spent)} spent, ${money(b.held)} reserved pending approval, `
        + `${money(b.available)} left of a ${money(b.reserved)} budget`}
        className="meter__bar"
      >
        <span className="is-spent" style={{ width: pct(b.spent) }} />
        <span className="is-held" style={{ width: pct(b.held) }} />
        <span className="is-free" style={{ width: pct(b.available) }} />
      </div>
      <dl className="meter__keys">
        <div><dt><i className="swatch is-spent" /> Spent</dt>
          <dd>{money(b.spent, b.currency)}</dd></div>
        <div><dt><i className="swatch is-held" /> Reserved / Pending</dt>
          <dd>{money(b.held, b.currency)}</dd></div>
        <div><dt><i className="swatch is-free" /> Left to spend</dt>
          <dd>{money(b.available, b.currency)}</dd></div>
        <div><dt>Total budget</dt>
          <dd>{money(b.reserved, b.currency)}</dd></div>
        <div><dt>Ends</dt>
          <dd>{(b.expires_at || '').slice(0, 10)}</dd></div>
      </dl>
      {(b.revoked || b.frozen) && (
        <p className="meter__stop">
          {b.revoked ? 'You cancelled this budget. Nothing more can be spent.'
            : `This budget is frozen — ${b.freeze_reason}`}
        </p>
      )}
    </div>
  );
}

export function ErrorLine({ children }) {
  return <p className="error-line">{children}</p>;
}

// Committed markdown, already escaped by md(). The four documents this renders
// are files in this repo, never anything a caller can write.
export function Markdown({ text }) {
  return (
    <div className="prose" dangerouslySetInnerHTML={{ __html: md(text || '') }} />
  );
}

// Loading, then content, then a visible error with retry. Nothing on this site
// is allowed to fail into a blank box.
export function useAsync(work, deps = []) {
  const [state, setState] = useState({ loading: true, error: null, data: null });
  const [nonce, setNonce] = useState(0);
  const retry = () => setNonce(n => n + 1);

  useEffect(() => {
    let live = true;
    setState(s => ({ ...s, loading: true, error: null }));
    Promise.resolve()
      .then(work)
      .then(data => live && setState({ data, loading: false, error: null }))
      .catch(e => live && setState({ error: e.message, loading: false, data: null }));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);
  return { ...state, retry };
}

export function Async({ state, height = '6rem', children }) {
  if (state.loading) return <Skeleton height={height} />;
  if (state.error) {
    return (
      <div className="flex flex-wrap items-center gap-3 py-2">
        <ErrorLine>could not load this — {state.error}</ErrorLine>
        {state.retry && (
          <button type="button" className="btn btn--outline text-xs px-2 py-1" onClick={state.retry}>
            Retry
          </button>
        )}
      </div>
    );
  }
  return children(state.data);
}

// The live decision feed with exponential backoff on errors and clean recovery.
export function useFeed(api, onChange) {
  const [rows, setRows] = useState([]);
  const [error, setError] = useState(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    let cursor = null, timer = null, live = true, backoff = 2000;
    async function tick() {
      try {
        const data = await api('/api/feed' + (cursor === null ? '' : '?after=' + cursor));
        if (!live) return;
        cursor = data.cursor;
        setError(null);
        backoff = 2000;
        if (data.records.length) {
          setRows(prev => [...prev, ...data.records].slice(-40));
          if (onChangeRef.current) onChangeRef.current();
        }
      } catch (e) {
        if (live) {
          setError(e.message);
          backoff = Math.min(backoff * 2, 16000);
        }
      }
      if (live) timer = setTimeout(tick, document.hidden ? 10000 : backoff);
    }
    tick();
    const wake = () => { clearTimeout(timer); timer = setTimeout(tick, 200); };
    document.addEventListener('visibilitychange', wake);
    return () => {
      live = false;
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', wake);
    };
  }, [api]);

  return { rows, error };
}
