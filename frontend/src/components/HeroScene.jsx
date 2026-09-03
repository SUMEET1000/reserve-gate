import { useEffect, useRef } from 'react';
import * as THREE from 'three';

// The hero, as one scene: an animated cobalt field and the glass portal that
// bends it. They are together in here rather than in a canvas plus an SVG layer
// because the field is what makes the glass legible - transmission through a
// blank white page shows nothing, and the block reads as plaster. Give it
// something with structure to refract and the same material reads as glass.
//
// The portal is the product: money goes in one side and only comes out if the
// policy lets it. Everything the field does - drift, follow the pointer, move
// with the scroll - is visible twice, once straight and once bent, and the
// difference between those two is the whole picture.
//
// Procedural throughout. No mesh file, no HDR, no texture request; the build
// plan's cold-start budget (E21, 32.2 s measured) has no room for an asset the
// page could start without.

// The canvas edge is invisible only while the field is exactly --color-paper, so
// both colours are read from the page's own tokens rather than copied here. A
// second copy of a colour is a second thing to forget, and a skin that swaps the
// tokens repaints the canvas with them for free. The literals are The Orbit
// Sheet's, used only when the stylesheet has not parsed yet.
const FIELD_FALLBACK = 0xf0f0ee;
const RING_FALLBACK = 0x900020;

function token(name, fallback) {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v ? new THREE.Color(v).getHex() : fallback;
  } catch {
    return fallback;
  }
}

// Walks a closed polygon and replaces each corner with a quadratic fillet. A
// sharp corner on a refractive solid throws a hard black caustic; a filleted
// one draws the long curved highlight that makes cast glass look cast.
export function roundedShape(points, radius) {
  const shape = new THREE.Shape();
  const n = points.length;
  for (let i = 0; i < n; i++) {
    const [px, py] = points[(i - 1 + n) % n];
    const [cx, cy] = points[i];
    const [nx, ny] = points[(i + 1) % n];
    const inLen = Math.hypot(px - cx, py - cy);
    const outLen = Math.hypot(nx - cx, ny - cy);
    const r = Math.min(radius, inLen / 2, outLen / 2);
    if (i === 0) shape.moveTo(cx + ((px - cx) / inLen) * r, cy + ((py - cy) / inLen) * r);
    else shape.lineTo(cx + ((px - cx) / inLen) * r, cy + ((py - cy) / inLen) * r);
    shape.quadraticCurveTo(cx, cy, cx + ((nx - cx) / outLen) * r, cy + ((ny - cy) / outLen) * r);
  }
  shape.closePath();
  return shape;
}

// Two posts and a lintel, as one closed outline.
function gateShape(w = 1.58, h = 2.42, t = 0.40) {
  return roundedShape([
    [-w, -h], [-w, h], [w, h], [w, -h],
    [w - t, -h], [w - t, h - t], [-(w - t), h - t], [-(w - t), -h],
  ], 0.035);
}

// The room the glass reflects, and it is mostly the page. A bright studio makes
// every face throw back white and the solid reads as plaster however clear the
// material actually is; a room the colour of the paper it stands on disappears
// into the page and leaves only what glass is supposed to show - the bright
// edge, the caustic, and the field bending behind it.
//
// The three accents earn their place: one white strip for the specular running
// down a post, one cobalt strip so the highlights belong to this palette, and
// one dark strip so the bevels have something to go dark against.
export function buildEnvironment(renderer) {
  const c = document.createElement('canvas');
  c.width = 256;
  c.height = 128;
  const g = c.getContext('2d');
  const sky = g.createLinearGradient(0, 0, 0, 128);
  sky.addColorStop(0.00, '#ffffff');
  sky.addColorStop(0.30, '#fdfcfa');
  sky.addColorStop(0.42, '#efece5');
  sky.addColorStop(0.499, '#ddd8ce');
  sky.addColorStop(0.501, '#11100e');   // the horizon: a hard edge, not a blend
  sky.addColorStop(0.62, '#171411');
  sky.addColorStop(0.80, '#211b17');
  sky.addColorStop(1.00, '#2a221e');
  g.fillStyle = sky;
  g.fillRect(0, 0, c.width, c.height);
  // Two narrow vertical strips of pure white: these are the hard specular
  // streaks that run down a post, and they only read as streaks while the rest
  // of the room stays the colour of the page.
  g.fillStyle = '#ffffff';
  g.fillRect(22, 8, 26, 54);     // the big soft-box, and the specular that runs a post
  g.fillRect(150, 14, 16, 46);
  g.fillRect(226, 20, 10, 36);
  g.fillStyle = '#1a1815';
  g.fillRect(96, 4, 22, 44);     // a dark gap between the lights, so an edge can go dark
  g.fillStyle = '#900020';
  g.fillRect(186, 66, 40, 8);    // one crimson bounce, so the highlights belong here

  const tex = new THREE.CanvasTexture(c);
  tex.mapping = THREE.EquirectangularReflectionMapping;
  tex.colorSpace = THREE.SRGBColorSpace;
  const pmrem = new THREE.PMREMGenerator(renderer);
  const env = pmrem.fromEquirectangular(tex).texture;
  pmrem.dispose();
  tex.dispose();
  return env;
}

// The animated field. Concentric orbit rings centred on the gate itself, drawn
// as crimson hairlines on cream, the way a drawing sets its subject at the
// centre of its own measurement. Lines rather than a wash because a refracted
// line shows exactly where it was bent and a refracted wash shows nothing.
//
// The rings are elliptical, not circular: they lie on the ground plane and are
// seen from slightly above, so a true circle would read as a flat target.
//
// It is deliberately faint, and absent on the left: the headline sits over that
// half and a moving pattern behind display type is noise, not depth.
const FIELD_VERT = /* glsl */ `
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const FIELD_FRAG = /* glsl */ `
  precision highp float;
  varying vec2 vUv;
  uniform float uTime;
  uniform float uScroll;
  uniform vec2  uPointer;    // -1..1, already damped on the CPU
  uniform vec2  uSpan;       // plane size in world units, to keep lines square
  uniform vec3  uPaper;
  uniform vec3  uInk;
  uniform float uTextSide;   // 0 = fade the left, 1 = no fade (mobile)
  uniform vec2  uCenter;     // the gate, projected onto this plane

  // Distance from the gate, squashed in y so the rings sit on the ground plane
  // rather than facing the camera. One contour of this is one orbit.
  float surface(vec2 p) {
    vec2 d = p - uCenter;
    d.y /= 0.42;
    return length(d);
  }

  void main() {
    vec2 p = (vUv - 0.5) * uSpan;
    p.y += uScroll * 1.6;

    // The pointer pulls the field toward it and thins the lines as it passes -
    // the same lens the glass is, in two dimensions, so the two read as one
    // material rather than as a background and a foreground.
    // The pointer bends the rings it passes, and only just. At 1.15 the whole
    // field swam after the cursor, which on a drawing reads as a bug.
    vec2 toM = p - uPointer * uSpan * 0.5;
    float d = length(toM);
    float pull = exp(-d * d * 0.055);
    p -= normalize(toM + 1e-5) * pull * 0.32;

    // One ring every 1.05 world units, drifting outward slowly enough that the
    // motion is felt rather than watched. The spacing is what makes the glass
    // legible: at 1.55 only three or four rings crossed a post, and 25 mm of
    // clear glass with almost nothing behind it reads as plaster however
    // correct the material is. 0.62 was the other end of that and read as wood
    // grain rather than as a drawing; widened to 1.05 on 1 Sept 2026, with the
    // falloff raised to 0.075 so the outer field thins out faster.
    float band = surface(p) / 1.05 - uTime * 0.055;
    float dist = abs(fract(band) - 0.5);
    float w = fwidth(band) * 1.25;
    float line = 1.0 - smoothstep(0.0, max(w, 0.0008), dist);

    // Every fourth ring is drawn heavier, the way a drawing thickens its
    // major divisions so the eye can count without reading a number.
    float major = step(0.5, 1.0 - abs(fract(band * 0.25) - 0.5) * 4.0);
    float weight = mix(0.62, 1.0, major);

    // The rings fade out with distance from the gate, so the sheet has a centre
    // and the far corners stay paper.
    float falloff = exp(-surface(p) * 0.075);

    // A local contrast lift gives the transmitted material enough structure to
    // bend without making the whole sheet louder.
    float glassZone = exp(-surface(p) * 0.35);
    float strength = (0.30 + glassZone * 0.10 + pull * 0.14) * weight * falloff;
    // Nothing at all under the text column, building only as it nears the
    // portal. On a narrow screen the copy sits below the portal, not beside it,
    // so there is no column to protect.
    float sideFade = mix(smoothstep(0.40, 0.74, vUv.x), 1.0, uTextSide);
    // Hold the very edges of the section down so the plane has no visible rim.
    float edge = smoothstep(0.0, 0.10, vUv.x) * smoothstep(1.0, 0.90, vUv.x)
               * smoothstep(0.0, 0.09, vUv.y) * smoothstep(1.0, 0.91, vUv.y);

    // No ground wash: the sheet is paper everywhere, and the only mark on it is
    // the ring. A tint behind the gate would be the thing that makes cast glass
    // read as plaster.
    vec3 col = mix(uPaper, uInk, line * strength * sideFade * edge);
    gl_FragColor = vec4(col, 1.0);
  }
`;

// Builds the scene into `host` and returns its teardown. Split out of the
// effect so it can be started when the browser is idle rather than inside
// React's commit: PMREM plus the transmission shader compile blocked the main
// thread for about a second, and the page could not be clicked until it
// finished (measured 31 Aug 2026, harness/perf_check.py: GET / at 1640 ms
// against a 500 ms budget, 130 ms once this moved off the critical path and the
// compile went async).
function mount(host) {
  // A refusal to run is not a failure state: the headline beside this canvas is
  // the content, and it is complete without any of it.
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({
      // MSAA on top of an already supersampled buffer pays twice for one edge.
      antialias: window.devicePixelRatio < 1.5,
      alpha: false,
      // Named explicitly: 'high-performance' wakes the discrete GPU and cost
      // 940 ms of first paint on this laptop, measured 31 Aug 2026.
      powerPreference: 'default',
    });
  } catch {
    return null;
  }
  if (!renderer.getContext()) return null;

  const FIELD = token('--color-paper', FIELD_FALLBACK);
  const RING = token('--color-blue', RING_FALLBACK);

  const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // Above ~1.75 the extra pixels buy nothing through blurred glass and cost
  // real frames on a 144 Hz panel, where a frame is 6.9 ms and not 16.7.
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
  renderer.setClearColor(FIELD, 1);
  // No tone mapping. It earns its place on an HDR environment; this room is a
  // 256x128 canvas that never exceeds 1.0, so it was spending contrast it had
  // nothing to spend it on. It was not what made the paper the wrong cream -
  // that was the shader's own colour space, and the fix is at uPaper.
  renderer.toneMapping = THREE.NoToneMapping;
  // Transmission re-renders the scene into an offscreen target every frame.
  // Half resolution is invisible through 30 mm of glass: raising it to 1.0 was
  // tried against the plaster look on 31 Aug 2026 and changed nothing a
  // screenshot could show, so it is not what the milkiness was. The env map and
  // the faintness of the field behind the portal were.
  renderer.transmissionResolutionScale = 0.5;
  renderer.domElement.style.cssText =
    'display:block;width:100%;height:100%;opacity:0;transition:opacity .6s ease';
  host.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.environment = buildEnvironment(renderer);

  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 60);
  camera.position.set(0, 0, 9);

  const FIELD_Z = -6;
  const uniforms = {
    uTime: { value: 0 },
    uScroll: { value: 0 },
    uPointer: { value: new THREE.Vector2() },
    uSpan: { value: new THREE.Vector2(20, 12) },
    // Read as already-encoded sRGB, not converted. This is a raw ShaderMaterial:
    // it writes gl_FragColor straight into an sRGB drawing buffer with no
    // colorspace_fragment include, so a colour-managed THREE.Color hands it a
    // linear component that is then written as if it were sRGB. Measured
    // 1 Sept 2026: #f0f0ee arrived on screen at 222,222,218 against the CSS
    // page's 237,237,236, which is why the hero sat on a different cream to
    // every section under it.
    uPaper: { value: new THREE.Color().setHex(FIELD, THREE.LinearSRGBColorSpace) },
    uInk: { value: new THREE.Color().setHex(RING, THREE.LinearSRGBColorSpace) },
    uTextSide: { value: 0 },
    uCenter: { value: new THREE.Vector2() },
  };
  const field = new THREE.Mesh(
    new THREE.PlaneGeometry(1, 1),
    new THREE.ShaderMaterial({
      uniforms, vertexShader: FIELD_VERT, fragmentShader: FIELD_FRAG, depthWrite: false,
    })
  );
  field.position.z = FIELD_Z;
  scene.add(field);

  const gateGeometry = new THREE.ExtrudeGeometry(gateShape(), {
    depth: 1.55,
    bevelEnabled: true,
    // One segment is the whole point: it is a chamfer, so the arris is a flat
    // facet with its own normal rather than a smoothly turned corner.
    bevelThickness: 0.115,
    bevelSize: 0.105,
    bevelSegments: 1,
    curveSegments: 3,
  });
  // Per-face normals. Shared vertices average the facets back into a curve,
  // which is exactly the plaster look; splitting them keeps every facet flat.
  gateGeometry.computeVertexNormals();

  const gate = new THREE.Mesh(
    gateGeometry,
    new THREE.MeshPhysicalMaterial({
      color: 0xffffff,
      transmission: 1,
      // Thin, so absorption barely tints the body. Thickness here buys the
      // bend, not the colour, and colour is what stops it reading as crystal.
      thickness: 3.1,
      ior: 2.05,
      // Real glass splits white light. This is what fringes the contour lines
      // warm on one edge and cold on the other where they cross a post.
      dispersion: 1.5,
      roughness: 0.0,
      metalness: 0,
      // The room is mostly paper-white, so every unit of this is white haze laid
      // over the body. Enough for the edges to catch, not enough to fog the pane.
      envMapIntensity: 0.45,
      attenuationColor: new THREE.Color(0xffffff),
      // Long enough that 25 mm of glass takes almost no colour out. The bend is
      // thickness; the tint is this, and the tint is what kills the crystal.
      attenuationDistance: 14.0,
      // A thin lacquer, only for the crisp line along each arris. At 1 it
      // becomes a second mirror and the block turns to plaster, and even 0.4
      // veiled the body against a white page.
      clearcoat: 0.10,
      clearcoatRoughness: 0.0,
    })
  );
  gate.geometry.center();
  scene.add(gate);

  const key = new THREE.DirectionalLight(0xffffff, 1.1);
  key.position.set(3.2, 4.4, 5.5);
  scene.add(key);
  // The rim was cobalt when the page was; it is the sheet's own crimson now, so
  // the one coloured edge on the glass belongs to the only ink on the page.
  const rim = new THREE.DirectionalLight(0x8c3040, 1.2);
  rim.position.set(-4.5, -1.4, -3.2);
  scene.add(rim);

  // Half the visible height at a given distance, for both the fitting below and
  // the plane that has to cover the frame exactly.
  const halfAt = d => Math.tan((camera.fov * Math.PI) / 360) * d;
  let gateBaseY = 0;

  function resize() {
    const { clientWidth: w, clientHeight: h } = host;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;

    // Solve the camera distance rather than guessing it at a breakpoint: the
    // portal is tilted, so the box that has to fit is larger than either of its
    // dimensions and changes with every canvas shape the layout makes.
    // The half-heights the portal has to fit inside. Raised from 3.35 / 2.45 on
    // 1 Sept 2026: the Orbit Sheet needs room for rings all the way around the
    // subject, and a portal that fills its half of the frame leaves none. This
    // is the one number that decides how much of the drawing is drawing.
    const tan = Math.tan((camera.fov * Math.PI) / 360);
    camera.position.z = Math.max(5.10 / tan, 3.70 / (tan * camera.aspect));
    camera.updateProjectionMatrix();

    // Wide enough for the text column and the portal side by side: park the
    // portal in the right half. Narrow, there is only one column, so centre it.
    const wide = camera.aspect > 1.35;
    const halfH = halfAt(camera.position.z);
    gate.position.x = wide ? halfH * camera.aspect * 0.36 : 0;
    // Narrow, the canvas still covers the whole section but the copy stacks
    // under the portal, so the portal has to move up out from behind it.
    gateBaseY = wide ? 0 : halfH * 0.52;
    uniforms.uTextSide.value = wide ? 0 : 1;

    const fieldHalfH = halfAt(camera.position.z - FIELD_Z);
    const span = new THREE.Vector2(fieldHalfH * camera.aspect * 2, fieldHalfH * 2);
    field.scale.set(span.x, span.y, 1);
    uniforms.uSpan.value.copy(span);
  }
  resize();

  // Pointer and scroll are read here and applied in the frame, never applied
  // directly: a handler that touches the scene runs as often as the mouse
  // reports and drops frames doing it.
  const aim = new THREE.Vector2();
  const eased = new THREE.Vector2();
  function onPointer(e) {
    const r = host.getBoundingClientRect();
    aim.set(((e.clientX - r.left) / r.width) * 2 - 1, ((e.clientY - r.top) / r.height) * 2 - 1);
  }

  let scroll = 0;
  function onScroll() {
    scroll = Math.max(-1, Math.min(1, -host.getBoundingClientRect().top / window.innerHeight));
  }
  onScroll();

  // Off screen the loop stops outright. A hero that keeps a GPU busy while
  // someone reads five sections down is a battery bug.
  let visible = true;
  let started = false;
  let disposed = false;
  const io = new IntersectionObserver(([e]) => {
    visible = e.isIntersecting;
    if (visible && started && !still) tick();
  }, { threshold: 0 });
  io.observe(host);

  let frame = 0;
  const clock = new THREE.Clock();
  // Seconds for a follow to cover ~63% of the remaining distance. The old
  // fixed per-frame fraction converged 2.4x faster on a 144 Hz panel than on a
  // 60 Hz one, so the same build felt sharp on one machine and sluggish on
  // another for reasons no setting explained.
  const FOLLOW_TAU = 0.16;

  function draw() {
    const dt = Math.min(clock.getDelta(), 0.1);   // a backgrounded tab returns a huge delta
    const t = clock.getElapsedTime();
    eased.lerp(aim, 1 - Math.exp(-dt / FOLLOW_TAU));

    uniforms.uTime.value = t;
    uniforms.uScroll.value = scroll;
    uniforms.uPointer.value.set(eased.x, -eased.y);

    // Square to the camera, the way the reference draws it: a drawing shows its
    // subject in elevation, and a tumbling object is a product render. What is
    // left is a few degrees of sway either side of front, and a pointer follow
    // small enough to feel like parallax rather than like dragging the object.
    gate.rotation.y = Math.sin(t * 0.30) * 0.045 + eased.x * 0.075;
    gate.rotation.x = Math.sin(t * 0.23) * 0.018 - eased.y * 0.035;
    gate.rotation.z = 0;
    // The portal holds its place. It used to ride the scroll by 0.8 units;
    // rejected 2 Sept 2026 - the subject of a drawing does not move when the
    // sheet is read. The scroll still reaches the contour field through
    // uScroll, which is the ground drifting, not the subject.
    gate.position.y = gateBaseY + Math.sin(t * 0.55) * 0.04;

    // Where the gate lands on the ring plane, seen from the camera. The rings
    // are centred on the subject, so this is recomputed every frame rather than
    // at resize: the gate drifts and rides the scroll, and rings that stayed put
    // would read as wallpaper behind it instead of measurement around it.
    const t2 = (camera.position.z - FIELD_Z) / camera.position.z;
    uniforms.uCenter.value.set(gate.position.x * t2, gate.position.y * t2);

    renderer.render(scene, camera);
  }
  function tick() {
    cancelAnimationFrame(frame);
    if (!visible || disposed) return;
    draw();
    frame = requestAnimationFrame(tick);
  }

  const ro = new ResizeObserver(() => { resize(); if (started && still) draw(); });
  ro.observe(host);

  // MeshPhysicalMaterial with transmission, dispersion and iridescence is one of
  // the largest shaders three ships, and compiling it on the first render blocks
  // everything. compileAsync hands it to KHR_parallel_shader_compile where the
  // driver has it, and simply resolves later where it does not.
  renderer.compileAsync(scene, camera).then(() => {
    if (disposed) return;
    started = true;
    renderer.domElement.style.opacity = '1';
    if (still) {
      // One frame, held. The shape and the refraction are the content; the
      // drift is the part someone asked us to switch off.
      draw();
    } else {
      window.addEventListener('pointermove', onPointer, { passive: true });
      window.addEventListener('scroll', onScroll, { passive: true });
      tick();
    }
  });

  return () => {
    disposed = true;
    cancelAnimationFrame(frame);
    io.disconnect();
    ro.disconnect();
    window.removeEventListener('pointermove', onPointer);
    window.removeEventListener('scroll', onScroll);
    scene.environment?.dispose();
    gate.geometry.dispose();
    gate.material.dispose();
    field.geometry.dispose();
    field.material.dispose();
    renderer.dispose();
    renderer.domElement.remove();
  };
}

export default function HeroScene({ className = '' }) {
  const hostRef = useRef(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let teardown = null;
    let cancelled = false;
    const start = () => { if (!cancelled) teardown = mount(host); };
    // The timeout is the point: on a slow machine idle may never arrive, and
    // the hero has to appear anyway.
    const idle = 'requestIdleCallback' in window;
    const id = idle ? requestIdleCallback(start, { timeout: 900 }) : setTimeout(start, 200);
    return () => {
      cancelled = true;
      if (idle) cancelIdleCallback(id); else clearTimeout(id);
      teardown?.();
    };
  }, []);

  // aria-hidden with no fallback text: every word this illustrates is in the
  // headline over it, and a described decoration is one more thing between a
  // screen-reader user and the demo button.
  return <div ref={hostRef} aria-hidden="true" className={className} />;
}
