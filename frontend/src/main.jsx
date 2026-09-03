import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

import Landing from './pages/Landing.jsx';
import Demo from './pages/Demo.jsx';
import Attack from './pages/Attack.jsx';
import Mutate from './pages/Mutate.jsx';
import Trace from './pages/Trace.jsx';
import Rules from './pages/Rules.jsx';
import Evidence from './pages/Evidence.jsx';

// One bundle for seven pages. The server keeps serving seven separate HTML
// files at seven exact paths, because PUBLIC_PATHS is a closed set and a
// client-side router would need a catch-all route to replace it. The shell
// names which page it is; this picks the component.
const ROUTES = {
  landing: Landing,
  demo: Demo,
  attack: Attack,
  mutate: Mutate,
  trace: Trace,
  rules: Rules,
  evidence: Evidence,
};

const mount = document.getElementById('root');
const Page = ROUTES[document.body.dataset.page];

document.body.insertAdjacentHTML('afterbegin', `<!--
THESIS: the gate is the object at the centre of a technical drawing, every rule an orbit around it; it refuses the dark-gradient developer-tool hero.
OWN-WORLD: cream drafting paper, one crimson spent only on orbit rings and dimension marks, hairline rules with corner tick crosses, a ghost layer of body text, Didone display over letterspaced mono labels.
STORY: the visitor reads one promise, sees the gate bend the rings that pass behind it, and goes to follow a real purchase through it.
FIRST VIEWPORT: headline in three Didone lines at left over the sub-paragraph and one bordered action; the live glass gate at right, centred in concentric crimson ellipses with dimension chains; four mono labels pinned to the sheet corners.
FORM: The Orbit Sheet, pinned from references/creation.jpg and locked over eight dealt directions; round seed key 29522681.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
-->`);

// One observer for every section on every page. Sections declare themselves
// with data-reveal and are hidden by CSS until they have been seen once; a
// per-page hook would mean seven copies of the same six lines. `once` is
// deliberate - a section that re-animates on the way back up is a section
// nobody can read while scrolling.
// Reveal is progressive enhancement, and the CSS only hides anything once this
// class is on the root. Without it a reader with JavaScript off - or one whose
// bundle failed - would get a page of invisible sections.
document.documentElement.classList.add('js-reveal');

// Inertial scroll - the reference site's most recognisable behaviour, and the
// one thing about it that is felt rather than seen. Lenis (MIT) rather than a
// hand-rolled wheel hijack: intercepting the wheel badly breaks keyboard paging,
// trackpad momentum and assistive tooling, which is why a library this small
// exists at all. Nothing of theirs is copied; the technique is not the code.
//
// Two gates. It runs only under the night skin - which every shell now sets, so
// in practice it is always on - and never under prefers-reduced-motion, since a
// lerped scroll is exactly the vestibular trigger that setting is for. The skin
// test stays: removing data-skin from the shells restores The Orbit Sheet, and
// its scrolling has to come back with it.
//
// The import is dynamic but is still inside app.js: vite.config.js sets
// inlineDynamicImports because
// PUBLIC_PATHS is a closed route set (G1) and a second chunk would need a second
// route. Measured 2 Sept 2026 - app.js 799.58 to 820.07 kB, about 6 kB gzipped,
// paid by both skins.
if (document.documentElement.dataset.skin === 'night'
    && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
  import('lenis').then(({ default: Lenis }) => {
    const lenis = new Lenis({ duration: 1.05, smoothWheel: true });
    const raf = (t) => { lenis.raf(t); requestAnimationFrame(raf); };
    requestAnimationFrame(raf);
  }).catch(() => {
    // A failed chunk must cost the skin its scroll feel and nothing else; the
    // page scrolls natively and every other behaviour is unchanged.
  });
}

const reveal = new IntersectionObserver(entries => {
  for (const e of entries) {
    if (!e.isIntersecting) continue;
    e.target.classList.add('is-in');
    reveal.unobserve(e.target);
  }
}, { rootMargin: '0px 0px -12% 0px', threshold: 0.05 });

const watchReveals = () => document.querySelectorAll('[data-reveal]:not(.is-in)')
  .forEach(el => reveal.observe(el));

// A failsafe, and it is not theoretical: a full-page screenshot resizes the
// viewport past every threshold before the observer has run once, and the page
// captured with three sections blank. Anything still hidden after two seconds
// is shown, whatever the observer thinks.
setTimeout(() => {
  document.querySelectorAll('[data-reveal]:not(.is-in)').forEach(el => el.classList.add('is-in'));
}, 2000);

if (mount && Page) {
  createRoot(mount).render(<StrictMode><Page /></StrictMode>);
  // After the first paint, and again whenever a route swaps content in.
  requestAnimationFrame(watchReveals);
  new MutationObserver(watchReveals).observe(mount, { childList: true, subtree: true });
} else if (mount) {
  // A shell naming a page this bundle does not have is a build mistake, not a
  // visitor's problem, so it says so instead of leaving an empty page.
  mount.textContent = 'This page is not in the build: ' + document.body.dataset.page;
}
