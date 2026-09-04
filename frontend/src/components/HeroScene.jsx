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
  // 512x256 rather than 256x128: at roughness 0 the sharpest mip is sampled
  // almost directly, and a coarse equirect turns a specular streak into a
  // staircase along the arris. The PMREM pass is a one-off at mount.
  c.width = 512;
  c.height = 256;
  const g = c.getContext('2d');
  // A dark studio, because the page is one. The sheet reads near-black, so a
  // cream room put a bright veil over every face and the solid came back as
  // grey plaster however clear the material was.
  //
  // The bright band sits on the horizon and the room goes dark above and below
  // it, and that placement is the whole Fresnel effect: a ray leaving a vertical
  // face at a grazing angle travels almost horizontally and so samples the
  // equator of this map. With a dark band there the silhouette reflected
  // nothing and the edges read as dull grey.
  const sky = g.createLinearGradient(0, 0, 0, 256);
  sky.addColorStop(0.00, '#23262b');   // a cool ceiling bounce
  sky.addColorStop(0.30, '#101215');
  sky.addColorStop(0.44, '#5c646e');
  sky.addColorStop(0.492, '#e8eef5');
  sky.addColorStop(0.500, '#ffffff');  // the horizon band, and the rim it draws
  sky.addColorStop(0.508, '#cdd6e0');
  sky.addColorStop(0.58, '#2a2e34');
  sky.addColorStop(1.00, '#08090a');
  g.fillStyle = sky;
  g.fillRect(0, 0, c.width, c.height);

  // The softboxes. Pure white and well over the room, so on an ACES curve they
  // land as highlights that roll off instead of flat clipped patches.
  g.fillStyle = '#ffffff';
  g.fillRect(44, 16, 52, 108);    // the big key, and the streak that runs a post
  g.fillRect(300, 28, 32, 92);
  g.fillRect(452, 40, 20, 72);
  // One narrow hot strip, thin enough to read as the single sharp line that
  // travels along an arris as the portal sways.
  g.fillRect(196, 34, 7, 118);
  // A cool fill opposite the key, so the shadow side is blue-grey rather than
  // dead black. This is where the cyan on the edges comes from.
  g.fillStyle = '#7fb4d8';
  g.fillRect(360, 96, 96, 26);
  // No strip of the page's ink in here. One was tried on 4 Sept 2026 so the
  // coloured edge would belong to the palette, and it came back as a flat lime
  // panel across the whole top of the lintel: an upward-facing surface mirrors
  // whatever is above it almost undistorted, so a saturated block in the room
  // is a saturated block on the object. Rejected. The colour on the edges comes
  // from dispersion and the rim light, which are angle-dependent and so stay on
  // the edges. Do not put a coloured shape back in this room.

  const tex = new THREE.CanvasTexture(c);
  tex.mapping = THREE.EquirectangularReflectionMapping;
  tex.colorSpace = THREE.SRGBColorSpace;
  const pmrem = new THREE.PMREMGenerator(renderer);
  const env = pmrem.fromEquirectangular(tex).texture;
  pmrem.dispose();
  tex.dispose();
  return env;
}

const FIELD_VERT = /* glsl */ `
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

// The animated field. Continuous, minimal kinetic streamlines flowing behind
// the glass gate, with a subtle, non-intrusive hover deflection and clarity lift.
// It is deliberately faint, and absent on the left: the headline sits over that
// half and a moving pattern behind display type is noise, not depth.
const FIELD_FRAG = /* glsl */ `
  precision highp float;
  varying vec2 vUv;
  uniform float uTime;
  uniform float uScroll;
  uniform vec2  uPointer;      // -1..1, damped on CPU
  uniform float uHoverActive;  // 0..1 smooth gate for hover interaction
  uniform vec2  uSpan;         // plane size in world units
  uniform vec3  uPaper;
  uniform vec3  uInk;
  uniform float uTextSide;     // 0 = fade the left (desktop), 1 = mobile
  uniform vec2  uCenter;       // gate center

  float gateDist(vec2 p) {
    vec2 d = p - uCenter;
    d.y /= 0.55;
    return length(d);
  }

  void main() {
    vec2 p = (vUv - 0.5) * uSpan;
    p.y += uScroll * 1.6;

    // Mouse in world coordinates
    vec2 mouse = uPointer * uSpan * 0.5;
    vec2 toMouse = p - mouse;
    float distToMouse = length(toMouse);

    // Minimal, subtle hover interaction:
    float hoverDist = 2.6;
    float hoverField = (1.0 - smoothstep(0.0, hoverDist, distToMouse)) * uHoverActive;
    float hoverCore = exp(-distToMouse * distToMouse * 0.70) * uHoverActive;

    // NEW HOVER EFFECT: Gentle magnetic contour pull (draws lines subtly toward cursor)
    vec2 pull = -toMouse * hoverCore * 0.14;
    vec2 pWarped = p + pull;

    // Localized harmonic resonance: subtle wave ripples along lines near pointer
    float hoverResonance = sin(distToMouse * 3.6 - uTime * 3.2) * hoverCore * 0.12;

    // CALM, MINIMAL CONTINUOUS ANIMATION:
    // Gentle harmonic waves flowing slowly across the section
    float t = uTime * 0.45;
    float w1 = sin(pWarped.x * 0.35 + t * 0.60) * 0.45;
    float w2 = cos(pWarped.x * 0.70 - t * 0.40 + 0.8) * 0.25;
    float w3 = sin(pWarped.x * 0.18 + pWarped.y * 0.25 + t * 0.30) * 0.20;
    float wave = w1 + w2 + w3 + hoverResonance;

    // Clean, architectural streamline spacing
    float spacing = 0.65;
    float lineCoord = (pWarped.y - wave * 0.60) / spacing;

    // Razor-sharp anti-aliased hairline
    float distToLine = abs(fract(lineCoord) - 0.5);
    float lw = max(fwidth(lineCoord), 0.0008) * 1.25;
    float lineShape = 1.0 - smoothstep(0.0, lw, distToLine);

    // Major rhythm: every 4th streamline is slightly accented
    float majorIndex = step(0.5, 1.0 - abs(fract(lineCoord * 0.25) - 0.5) * 4.0);
    float lineWeight = mix(0.75, 1.15, majorIndex);

    // Soft traveling kinetic pulse along the line: subtle, not blinding
    float pulse = exp(-pow(fract((pWarped.x * 0.10 - t * 0.35 + floor(lineCoord) * 0.33)) - 0.5, 2.0) * 220.0);
    float travelPulse = pulse * lineShape * 0.28;

    // Spatial falloff: natural fade away from gate so outer corners remain pure paper
    float gDist = gateDist(p);
    float falloff = exp(-gDist * 0.055);
    float glassZone = exp(-gDist * 0.30);

    // REFINED VISIBILITY (brighter, legible, clean, minimal):
    float baseLine = lineShape * lineWeight * 0.175 * falloff;
    float glassLines = glassZone * lineShape * 0.20;

    // NEW SUBTLE HOVER EFFECT:
    // Delicate micro-packets and coordinate ticks strictly along hovered streamlines
    float hoverPulse = exp(-pow(fract((pWarped.x * 0.55 - t * 1.6 + floor(lineCoord) * 0.45)) - 0.5, 2.0) * 100.0) * hoverField;
    float hoverTicks = step(0.72, sin(pWarped.x * 8.0 + floor(lineCoord) * 3.14)) * hoverField * 0.16;
    float hoverLineLift = lineShape * (hoverField * 0.16 + hoverPulse * 0.22 + hoverTicks);

    float totalPattern = baseLine + glassLines + travelPulse * falloff + hoverLineLift;

    // Text protection: left half strictly darkened on desktop
    float mobileCopyFade = mix(smoothstep(0.58, 0.40, vUv.y), 1.0, 1.0 - uTextSide);
    float sideFade = mix(smoothstep(0.38, 0.72, vUv.x), 1.0, uTextSide) * mobileCopyFade;

    // Canvas border fade
    float edge = smoothstep(0.0, 0.10, vUv.x) * smoothstep(1.0, 0.90, vUv.x)
               * smoothstep(0.0, 0.09, vUv.y) * smoothstep(1.0, 0.91, vUv.y);

    float totalMask = sideFade * edge;

    // Color grading:
    float isDark = step(0.5, 1.0 - (uPaper.r + uPaper.g + uPaper.b) * 0.3333);

    // Rest line color: crisp architectural silver-slate on dark, subtle crimson on light
    vec3 restLineCol = mix(uPaper, isDark > 0.5 ? vec3(0.56, 0.64, 0.70) : uInk, isDark > 0.5 ? 0.40 : 0.26);
    // Active hover line color: refined clear ink
    vec3 hoverLineCol = mix(restLineCol, uInk, 0.58);

    vec3 lineColor = mix(restLineCol, hoverLineCol, clamp(hoverField * 0.85 + pulse * 0.45, 0.0, 1.0));

    // The blend runs in sRGB, where a line drawn at half strength lands at half
    // the perceived distance between sheet and ink. Blending the same stop in
    // linear light puts it well above that - a visibly brighter, fatter line for
    // the same maths - so the two steps stay apart: mix here, convert after.
    vec3 col = mix(uPaper, lineColor, clamp(totalPattern * totalMask, 0.0, 1.0));

    // Then hand the pipeline true light, because this plane is drawn twice per
    // frame: once to the canvas, and once into the linear half-float target the
    // glass refracts. Writing an sRGB number straight out suited the canvas and
    // was read back as linear by the transmission sampler, where 0.067 is about
    // four times the 0.0066 it should be - which is why the sheet behind the
    // portal came out mid-grey while the same sheet beside it was correct, and
    // why the glass read as milky plastic however clean the material was.
    // Converting here and letting colorspace_fragment re-encode is a round trip
    // to the canvas and a correction to the target.
    gl_FragColor = sRGBTransferEOTF(vec4(col, 1.0));

    // No tonemapping_fragment beside it, deliberately. The canvas edge is
    // invisible only while this plane is exactly the CSS colour, and an ACES
    // curve over a flat page colour is not that colour. Tone mapping belongs to
    // the lit material, which has highlights to roll off; this has none.
    #include <colorspace_fragment>
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
  // ACES filmic, and it reaches the lit material only. The contour field is a
  // ShaderMaterial with no tonemapping_fragment include, so the sheet keeps the
  // exact colour the CSS page uses and only the glass is mapped. That is what
  // lets a specular run past 1.0 and roll off as a highlight instead of
  // clipping to a flat white patch along the arris.
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;
  // Transmission re-renders the scene into an offscreen target every frame, and
  // that target is what shows through the glass. At 0.5 the refracted lines
  // carried the blur of the buffer rather than the bend of the solid, which is
  // the difference between optical glass and frosted acrylic.
  renderer.transmissionResolutionScale = 1.0;
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
    uHoverActive: { value: 0 },
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
      // The optical path length through the solid, and so how far the field
      // behind it bends. Tuned against the page it sits on, which is near-black:
      // more thickness only sends the refracted ray further into a dark
      // background, so past this the solid stops reading as glass and starts
      // reading as a hole.
      thickness: 2.1,
      // Optical crown glass. 2.05 was diamond territory and threw total internal
      // reflection across most of the posts, which reads as a dark core.
      ior: 1.55,
      // Real glass splits white light, and three renders that by sampling the
      // transmission target three times at spread IORs. This is the whole
      // rainbow budget, and it lands on the chamfers where the path is longest.
      dispersion: 3.4,
      roughness: 0.0,
      metalness: 0,
      // Low, and lowered again once it was measured on real hardware. Every unit
      // of this is the room painted back onto the faces, and a face that shows
      // the room is a face you cannot see through. Software rendering hid how
      // much brighter this reads on a GPU; measured on an RTX 4050, 4 Sept 2026.
      envMapIntensity: 0.10,
      // Barely-there cyan, and the only colour in the material.
      attenuationColor: new THREE.Color(0xeaf8ff),
      // Short, so the glass reads dense rather than as a clear pane. Lowering
      // transmission below 1.0 would have read as milk instead, because what
      // transmission gives back below 1.0 is the white diffuse colour, not
      // density.
      attenuationDistance: 8.5,
      // No lacquer. Clearcoat is a second mirror over the whole surface and it
      // reflects at every angle rather than only at grazing ones - it was the
      // single thing that made the flat faces catch light. The crisp line along
      // an arris comes from the bevel's own normal and from dispersion.
      clearcoat: 0.0,
      clearcoatRoughness: 0.0,
      // A thin-film interference coat, thin enough to be seen only at grazing
      // angles. Dispersion colours what is behind the glass; iridescence colours
      // the glass's own edge, and only one of those can put a fringe on a bevel.
      iridescence: 0.11,
      iridescenceIOR: 1.38,
      // Wide, so the fringe cycles through cyan, magenta and warm across a
      // single chamfer instead of sitting on one hue.
      iridescenceThicknessRange: [100, 560],
      // Below the 0.5 that is physically correct for glass, on purpose. This
      // scales the whole specular lobe including its Fresnel term, so lowering
      // it is what makes the front faces stop showing the room while the
      // grazing silhouette still lifts.
      specularIntensity: 0.15,
      specularColor: new THREE.Color(0xffffff),
    })
  );
  gate.geometry.center();
  scene.add(gate);

  const key = new THREE.DirectionalLight(0xffffff, 0.30);
  key.position.set(3.2, 4.4, 5.5);
  scene.add(key);
  // Cool, and behind. A rim from behind hits only the faces turned away from
  // the camera, which are the grazing ones, so its whole contribution lands on
  // the silhouette and the chamfers. Cyan because that is the edge colour this
  // material is after, and putting it in the light rather than in the
  // attenuation keeps the body itself colourless.
  const rim = new THREE.DirectionalLight(0x66d9ff, 0.65);
  rim.position.set(-4.5, -1.4, -3.2);
  scene.add(rim);

  // Half the visible height at a given distance, for both the fitting below and
  // the plane that has to cover the frame exactly.
  const halfAt = d => Math.tan((camera.fov * Math.PI) / 360) * d;
  let gateBaseY = 0;
  let gateBaseX = 0;
  // Where the portal sits in the same -1..1 space the pointer is reported in,
  // so the draw loop can ask how near the cursor is to it without projecting
  // anything. Kept in sync with gateBaseX/gateBaseY by resize().
  let gateAimX = 0;
  let gateAimY = 0;

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
    gateBaseX = wide ? halfH * camera.aspect * 0.36 : 0;
    gate.position.x = gateBaseX;
    // Narrow, the canvas still covers the whole section but the copy stacks
    // under the portal, so the portal has to move up out from behind it.
    gateBaseY = wide ? 0 : halfH * 0.52;
    // Divide out the half-extents and the world position becomes the pointer's
    // own units. y flips because the pointer is measured from the top of the
    // host and the world is measured up from its middle.
    gateAimX = wide ? 0.36 : 0;
    gateAimY = -(gateBaseY / halfH);
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
  // Zero, not an off-screen sentinel. These are read straight into
  // gate.rotation every frame, so a 999 here is 999 * 0.075 = 75 radians of yaw
  // on the first frames and the portal loads face-down, unwinding only once the
  // pointer is moved. Nothing needs the sentinel: every pointer effect in the
  // field is multiplied by uHoverActive, which is 0 until the cursor is
  // actually inside the hero.
  const aim = new THREE.Vector2();
  const eased = new THREE.Vector2();
  let hoverTarget = 0;
  let hoverActive = 0;
  let scroll = 0;
  let cachedHostRect = { left: 0, top: 0, width: 1, height: 1 };

  function onPointer(e) {
    const inside =
      e.clientX >= cachedHostRect.left &&
      e.clientX <= cachedHostRect.left + cachedHostRect.width &&
      e.clientY >= cachedHostRect.top &&
      e.clientY <= cachedHostRect.top + cachedHostRect.height;
    hoverTarget = inside ? 1 : 0;
    aim.set(
      ((e.clientX - cachedHostRect.left) / cachedHostRect.width) * 2 - 1,
      ((e.clientY - cachedHostRect.top) / cachedHostRect.height) * 2 - 1,
    );
  }

  function onPointerLeave() {
    hoverTarget = 0;
  }

  function onScroll() {
    scroll = Math.max(-1, Math.min(1, -cachedHostRect.top / window.innerHeight));
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
  const FOLLOW_TAU = 0.11;

  function draw() {
    const dt = Math.min(clock.getDelta(), 0.1);   // a backgrounded tab returns a huge delta
    const t = clock.getElapsedTime();
    eased.lerp(aim, 1 - Math.exp(-dt / FOLLOW_TAU));

    hoverActive += (hoverTarget - hoverActive) * (1 - Math.exp(-dt / 0.15));
    uniforms.uHoverActive.value = hoverActive;

    uniforms.uTime.value = t;
    uniforms.uScroll.value = scroll;
    uniforms.uPointer.value.set(eased.x, -eased.y);

    // Square to the camera, the way the reference draws it: a drawing shows its
    // subject in elevation, and a tumbling object is a product render. What is
    // left is a few degrees of sway either side of front, and a pointer follow
    // small enough to feel like parallax rather than like dragging the object.
    // How near the cursor is to the portal, 1 on it and near 0 by the time the
    // pointer reaches the copy. Everything below that is worth doing only when
    // someone is actually at the object is scaled by this, so the hero is still
    // a drawing when read and an object when reached for.
    const dx = eased.x - gateAimX;
    const dy = eased.y - gateAimY;
    const hover = Math.exp(-(dx * dx + dy * dy) * 3.2) * hoverActive;
    const reach = 1.0 + hover * 1.6;

    gate.rotation.y = Math.sin(t * 0.30) * 0.045 + eased.x * 0.155 * reach;
    // The nod. Its idle share is raised with the pointer share, so the object
    // still moves in this axis when nobody is pointing at it - a portal that
    // only tilts under the cursor reads as a control, not as a thing standing
    // there.
    gate.rotation.x = Math.sin(t * 0.23) * 0.048 - eased.y * 0.125 * reach;
    // A roll, and only while the cursor is on it. Held to about a degree: this
    // is the one axis a drawing never uses, so any more of it stops reading as
    // an object being turned and starts reading as a page that has slipped.
    gate.rotation.z = -eased.x * 0.020 * hover;
    gate.position.x = gateBaseX + eased.x * 0.105 * hover;
    // The portal holds its place. It used to ride the scroll by 0.8 units;
    // rejected 2 Sept 2026 - the subject of a drawing does not move when the
    // sheet is read. The scroll still reaches the contour field through
    // uScroll, which is the ground drifting, not the subject.
    // Two rates on the vertical, because one sine reads as a metronome. The
    // slow one is the drift and the fast one is a small breath on top of it,
    // and the offset phases keep them from lining up on a beat.
    gate.position.y = gateBaseY
      + Math.sin(t * 0.55) * 0.105
      + Math.sin(t * 0.91 + 1.7) * 0.032
      - eased.y * 0.145 * hover;

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

  const ro = new ResizeObserver(() => {
    resize();
    cachedHostRect = host.getBoundingClientRect();
    if (started && still) draw();
  });
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
      window.addEventListener('pointerleave', onPointerLeave, { passive: true });
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
    window.removeEventListener('pointerleave', onPointerLeave);
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
