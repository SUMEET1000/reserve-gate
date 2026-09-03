import { useEffect, useRef } from 'react';
import { PAGES } from '../lib/api.js';
import { Button, Roll } from './ui.jsx';

// The Orbit Sheet, second half. The landing page is the general arrangement:
// the object at the centre, the orbits around it, poster type. These five are
// the detail sheets filed behind it - same paper, same hairlines, same margin
// keys, and no orbit and no poster type anywhere. A detail sheet is read, not
// admired, and "proof pages too loud to read" is the failure mode this half
// exists to avoid.
//
// Nothing here survives from the previous world. The masthead illustration,
// the eyebrow above every title and the tab strip were the three pieces of
// borrowed SaaS chrome on this site; all three are gone rather than restyled.

export const DISCLAIMER =
  'Not an official Razorpay product and not affiliated with Razorpay in any way. '
  + 'A personal project built against their public test-mode APIs.';

export function Disclaimer() {
  return <p className="disclaimer">{DISCLAIMER}</p>;
}

export function Brand({ className = '' }) {
  return (
    <a href="/" className={`brand ${className}`}>
      <svg className="brand-mark" viewBox="0 0 24 34" aria-hidden="true">
        <path d="M12 1 22 7v20l-10 6L2 27V7Z" fill="none"
              stroke="currentColor" strokeWidth="1.4" />
        <path d="m12 1 1 32M2 7l11 6 9-6M3 27l10-6 9 6" fill="none"
              stroke="currentColor" strokeWidth="1" opacity=".8" />
      </svg>
      <span><Roll>reserve-gate</Roll></span>
    </a>
  );
}

export function SiteHeader({ children }) {
  return (
    <header className="site-header">
      <Brand />
      {children}
    </header>
  );
}

export function SiteFooter({ children }) {
  return (
    <footer className="site-footer">
      <div className="site-footer__row">{children}</div>
      <Disclaimer />
    </footer>
  );
}

// A drawing set numbers its sheets, and the number is how you know where you
// are in it without reading a title. That is the whole job the tab strip was
// doing badly: it was a row of underlined labels, which is chrome from a
// product this is not. Here the index is the sheet list, the current sheet is
// the one whose number is inked, and the order is the order of the routes.
const SHEETS = ['/', '/attack', '/mutate', '/trace', '/rules', '/evidence'];

function sheetNo(href) {
  const i = SHEETS.indexOf(href);
  return i < 0 ? '' : `${String(i + 1).padStart(2, '0')} / 06`;
}

function SheetIndex({ current }) {
  const currentLink = useRef(null);

  useEffect(() => {
    currentLink.current?.scrollIntoView({ block: 'nearest', inline: 'center' });
  }, []);

  return (
    <nav aria-label="Ways to check this yourself" className="sheet-index">
      <ol>
        {PAGES.filter(([href]) => href !== '/' && href !== '/demo').map(([href, label]) => (
          <li key={href} aria-current={href === current ? 'page' : undefined}>
            <a ref={href === current ? currentLink : undefined} href={href}>
              <span className="sheet-index__no" aria-hidden="true">
                {sheetNo(href).slice(0, 2)}
              </span>
              <span className="sheet-index__label">{label}</span>
            </a>
          </li>
        ))}
      </ol>
    </nav>
  );
}

// The title block. On a real drawing this sits in one corner of the sheet and
// carries the sheet's identity and its key figures; it is the only place a
// drawing puts words about itself. Replacing the illustration with it is not a
// like-for-like swap - a detail sheet has no hero image, and the page is
// quieter and shorter for not having one.
export function ProofPage({ title, lede, current, footer, children, brand, stats }) {
  return (
    <>
      <SiteHeader>
        <nav aria-label="Main navigation" className="site-nav">
          <a href="/" className="nav-link max-sm:hidden"><Roll>Home</Roll></a>
          <Button href="/demo" variant="primary">Try the guided demo</Button>
        </nav>
      </SiteHeader>

      <SheetIndex current={current} />

      <header className="title-block">
        {/* Registration ticks. The same four corner marks the landing sheet
            carries, so a visitor arriving here from it is on the same paper. */}
        <span className="title-block__tick" data-at="tl" aria-hidden="true" />
        <span className="title-block__tick" data-at="tr" aria-hidden="true" />

        <h1>{title}</h1>
        <p className="title-block__lede">{lede}</p>

        {/* The strip a drawing puts under its title: sheet number first, then
            whatever this sheet is actually measuring. Pages pass their own live
            figures; a page with none shows the sheet number alone rather than
            inventing a statistic to fill the row. */}
        <dl className="title-block__strip">
          <div>
            <dt>Sheet</dt>
            <dd>{sheetNo(current)}</dd>
          </div>
          {(stats || []).map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>

        {brand && <div className="title-block__brand">{brand}</div>}
      </header>

      <main className="sheet">{children}</main>

      {current === '/evidence' && (
        <section className="ots-proof-slot" aria-labelledby="ots-proof-title">
          <h2 id="ots-proof-title">OpenTimestamps proof</h2>
          <p>Generated after merge.</p>
        </section>
      )}

      <footer className="sheet-foot">
        <p>{footer}</p>
        <div className="sheet-foot__row">
          <Brand />
          <a href="/demo">Guided demo</a>
          <a href="/">Home</a>
        </div>
        <Disclaimer />
      </footer>
    </>
  );
}

export function RazorpayBrand({ children }) {
  return (
    <div className="rzp-chip">
      <img src="/razorpay-logo.png" alt="" />
      <span>{children}</span>
    </div>
  );
}

export { Button, Roll };
