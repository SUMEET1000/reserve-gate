import { StrictMode, useState, useEffect, useCallback, useRef } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

import Landing from './pages/Landing.jsx';
import Demo from './pages/Demo.jsx';
import Attack from './pages/Attack.jsx';
import Mutate from './pages/Mutate.jsx';
import Trace from './pages/Trace.jsx';
import Rules from './pages/Rules.jsx';
import Evidence from './pages/Evidence.jsx';
import { ErrorBoundary } from './components/ErrorBoundary.jsx';
import { PageTransitionOverlay } from './components/PageTransition.jsx';

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

if (typeof document !== 'undefined' && document.documentElement && !document.documentElement.dataset.skin) {
  document.documentElement.dataset.skin = 'night';
}

const PATH_TO_PAGE = {
  '/': 'landing',
  '/index.html': 'landing',
  '/demo': 'demo',
  '/demo.html': 'demo',
  '/attack': 'attack',
  '/attack.html': 'attack',
  '/mutate': 'mutate',
  '/mutate.html': 'mutate',
  '/trace': 'trace',
  '/trace.html': 'trace',
  '/rules': 'rules',
  '/rules.html': 'rules',
  '/evidence': 'evidence',
  '/evidence.html': 'evidence',
};

const PAGE_TO_PATH = {
  landing: '/',
  demo: '/demo',
  attack: '/attack',
  mutate: '/mutate',
  trace: '/trace',
  rules: '/rules',
  evidence: '/evidence',
};

const PAGE_TITLES = {
  landing: 'reserve-gate — AI Agent Spending Gateway',
  demo: 'Guided Demo — reserve-gate',
  attack: 'Try to Break It — reserve-gate',
  mutate: 'Remove a Rule — reserve-gate',
  trace: 'Follow the Money — reserve-gate',
  rules: 'The Rules — reserve-gate',
  evidence: 'The Proof — reserve-gate',
};

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

function App({ initialPage }) {
  const [currentPage, setCurrentPage] = useState(() => {
    const pathPage = PATH_TO_PAGE[window.location.pathname];
    return pathPage || initialPage || 'landing';
  });

  const [activeMode] = useState(() => {
    try {
      const q = new URLSearchParams(window.location.search).get('trans');
      if (q === 'shutter') return 'shutter'; // preserved backup
      return 'dissolve';
    } catch {
      return 'dissolve';
    }
  });

  const [stage, setStage] = useState('idle'); // 'idle' | 'closing' | 'opening'
  const timeoutRef = useRef(null);

  const navigateTo = useCallback((targetPath, { push = true, replay = false } = {}) => {
    const cleanPath = targetPath.split('?')[0].split('#')[0] || '/';
    const targetPage = PATH_TO_PAGE[cleanPath] || 'landing';

    if (!replay && targetPage === currentPage && !push) return;

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduced) {
      if (!replay) {
        setCurrentPage(targetPage);
        document.body.dataset.page = targetPage;
        document.body.className = targetPage;
        document.title = PAGE_TITLES[targetPage] || 'reserve-gate';
        if (push) window.history.pushState({ page: targetPage }, '', targetPath);
      }
      window.scrollTo(0, 0);
      requestAnimationFrame(watchReveals);
      return;
    }

    if (timeoutRef.current) clearTimeout(timeoutRef.current);

    setStage('closing');

    const closingDuration = activeMode === 'dissolve' ? 220 : activeMode === 'wipe' ? 240 : 260;
    const openingDuration = activeMode === 'dissolve' ? 280 : activeMode === 'wipe' ? 260 : 300;

    timeoutRef.current = setTimeout(() => {
      if (!replay) {
        setCurrentPage(targetPage);
        document.body.dataset.page = targetPage;
        document.body.className = targetPage;
        document.title = PAGE_TITLES[targetPage] || 'reserve-gate';
        if (push) window.history.pushState({ page: targetPage }, '', targetPath);
      }

      window.scrollTo(0, 0);
      requestAnimationFrame(watchReveals);

      setStage('opening');

      timeoutRef.current = setTimeout(() => {
        setStage('idle');
      }, openingDuration);
    }, closingDuration);
  }, [currentPage, activeMode]);

  useEffect(() => {
    const handleLinkClick = (e) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const a = e.target.closest('a');
      if (!a || !a.href) return;
      if (a.target && a.target !== '_self') return;
      if (a.hasAttribute('download')) return;

      const url = new URL(a.href, window.location.origin);
      if (url.origin !== window.location.origin) return;

      // Skip hash anchors on the same page
      if (url.pathname === window.location.pathname && url.hash) return;

      const targetPath = url.pathname;
      if (PATH_TO_PAGE[targetPath]) {
        e.preventDefault();
        navigateTo(targetPath + url.search + url.hash);
      }
    };

    const handlePopState = () => {
      const targetPath = window.location.pathname;
      if (PATH_TO_PAGE[targetPath]) {
        navigateTo(targetPath, { push: false });
      }
    };

    document.addEventListener('click', handleLinkClick);
    window.addEventListener('popstate', handlePopState);
    return () => {
      document.removeEventListener('click', handleLinkClick);
      window.removeEventListener('popstate', handlePopState);
    };
  }, [navigateTo]);

  useEffect(() => {
    requestAnimationFrame(watchReveals);
  }, [currentPage]);

  const CurrentPageComponent = ROUTES[currentPage] || Landing;

  return (
    <>
      <div className={`trans-content trans-content--${activeMode} trans-content--${stage}`}>
        <CurrentPageComponent />
      </div>
      <PageTransitionOverlay mode={activeMode} stage={stage} />
    </>
  );
}

const mount = document.getElementById('root');

if (mount) {
  createRoot(mount).render(
    <StrictMode>
      <ErrorBoundary>
        <App initialPage={document.body.dataset.page} />
      </ErrorBoundary>
    </StrictMode>
  );

  requestAnimationFrame(watchReveals);
  let revealTimer = null;
  new MutationObserver(() => {
    if (revealTimer) return;
    revealTimer = requestAnimationFrame(() => { watchReveals(); revealTimer = null; });
  }).observe(mount, { childList: true, subtree: true });
} else if (mount) {
  mount.textContent = 'This page is not in the build — ' + document.body.dataset.page;
}
