import { useState } from 'react';

export const TRANSITION_OPTIONS = [
  { id: 'dissolve', num: '01', name: 'Ethereal Blur', tag: 'Active', desc: 'Zero-overlay optical Gaussian focus & coordinate drift' },
  { id: 'shutter', num: '02', name: 'Shutter', tag: 'Backup', desc: 'Horizontal drafting blades & center hairline seam' },
  { id: 'curtain', num: '03', name: 'Dual Curtain', tag: '', desc: 'Vertical lateral panels meeting at center axis' },
  { id: 'wipe', num: '04', name: 'Horizon Wipe', tag: '', desc: 'Unidirectional continuous scanline sweep' },
];

export function PageTransitionHUD({ activeMode, onSelectMode, onReplay, isTransitioning }) {
  const [minimized, setMinimized] = useState(() => {
    try {
      const stored = localStorage.getItem('rg_trans_hud_min');
      if (stored !== null) return stored === '1';
      return true;
    } catch {
      return true;
    }
  });

  const toggleMin = () => {
    setMinimized(prev => {
      const next = !prev;
      try { localStorage.setItem('rg_trans_hud_min', next ? '1' : '0'); } catch {}
      return next;
    });
  };

  const activeOption = TRANSITION_OPTIONS.find(o => o.id === activeMode) || TRANSITION_OPTIONS[0];

  return (
    <aside className={`trans-pill-hud ${minimized ? 'is-min' : ''}`} aria-label="Transition Switcher">
      {minimized ? (
        <button type="button" onClick={toggleMin} className="trans-pill-toggle" title="Open transition selector">
          <span className="trans-pill-dot" />
          <span>{activeOption.name.toUpperCase()} {activeOption.tag ? `[${activeOption.tag}]` : ''}</span>
        </button>
      ) : (
        <div className="trans-pill-bar">
          <div className="trans-pill-header">
            <span className="trans-pill-dot" />
            <span className="trans-pill-title">CHOOSE TRANSITION</span>
            <button type="button" onClick={toggleMin} className="trans-pill-close" title="Minimize">✕</button>
          </div>
          <div className="trans-pill-options">
            {TRANSITION_OPTIONS.map(opt => {
              const active = activeMode === opt.id;
              return (
                <button
                  key={opt.id}
                  type="button"
                  onClick={() => onSelectMode(opt.id)}
                  className={`trans-pill-opt ${active ? 'is-active' : ''}`}
                  title={opt.desc}
                >
                  <span className="opt-num">{opt.num}</span>
                  <span className="opt-name">{opt.name}</span>
                  {opt.tag && <span className={`opt-tag ${opt.tag === 'Backup' ? 'opt-tag--backup' : ''}`}>{opt.tag}</span>}
                </button>
              );
            })}
            <button
              type="button"
              onClick={onReplay}
              disabled={isTransitioning}
              className="trans-pill-replay"
              title="Test transition on current page"
            >
              ↻ REPLAY
            </button>
          </div>
        </div>
      )}
    </aside>
  );
}

export function PageTransitionOverlay({ mode, stage }) {
  if (stage === 'idle') return null;

  return (
    <div className={`trans-overlay trans-overlay--${mode} trans-overlay--${stage}`} aria-hidden="true">
      {mode === 'shutter' && (
        <div className="shutter-stage">
          <div className="shutter-blade shutter-blade--top" />
          <div className="shutter-blade shutter-blade--bottom" />
          <div className="shutter-seam">
            <span className="shutter-seam__line" />
            <span className="shutter-seam__tick" />
          </div>
        </div>
      )}

      {mode === 'curtain' && (
        <div className="curtain-stage">
          <div className="curtain-panel curtain-panel--left" />
          <div className="curtain-panel curtain-panel--right" />
          <div className="curtain-axis">
            <span className="curtain-axis__line" />
            <span className="curtain-axis__tick" />
          </div>
        </div>
      )}

      {mode === 'wipe' && (
        <div className="wipe-stage">
          <div className="wipe-curtain" />
          <div className="wipe-beam" />
        </div>
      )}

      {/* mode === 'dissolve' has no overlay; all motion happens in trans-content */}
    </div>
  );
}
