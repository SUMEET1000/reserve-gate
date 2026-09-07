import { Component, Suspense, lazy } from 'react';

// The same chunk the landing hero already loads (web/app-HeroScene.js), so no
// page gains a second file and the route table is unchanged. three.js is still
// fetched after paint and never on the critical path.
const HeroScene = lazy(() => import('./HeroScene.jsx'));

// Decoration, so a failure is silence. The shared ErrorBoundary renders a full
// "Rendering Error" page, which is the wrong answer for a background.
class Quiet extends Component {
  state = { dead: false };
  static getDerivedStateFromError() {
    return { dead: true };
  }
  componentDidCatch() {}
  render() {
    return this.state.dead ? null : this.props.children;
  }
}

// The hero's contour field with nothing standing in it: the ground of a page
// header or of the landing close. Identical shader and identical pointer
// follow; what is dropped is the chrome, the room it reflects, and the two post
// passes that exist for it. The shader fades all four canvas edges to the page
// colour, so it has no border of its own to cut against the sheet.
// `centre` holds the middle back and draws both margins instead of clearing one
// side - the shape a centred block of copy needs, and the footers.
export function FieldBackdrop({ className = '', centre = false }) {
  return (
    <Quiet>
      <Suspense fallback={null}>
        <HeroScene gate={false} centre={centre} className={`field-backdrop ${className}`} />
      </Suspense>
    </Quiet>
  );
}
