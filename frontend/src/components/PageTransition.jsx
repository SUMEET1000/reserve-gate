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
