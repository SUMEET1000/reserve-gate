import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import { GammaCorrectionShader } from 'three/addons/shaders/GammaCorrectionShader.js';
import { heroSamples, isLowPowerHero } from '../lib/heroQuality.js';

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
//
// FROZEN, 4 Sept 2026. Sumeet: "I have finalized this current version of our 3D
// hero. This is final ... don't change anything about the current version of 3D
// Hero." Five rounds of his own instructions are in these numbers and each was
// measured; a plausible-looking tweak to any of them undoes work he has already
// signed off. What is frozen is everything to do with *the solid*:
//
//   gateShape        w 1.58, h 2.42, t 0.45   (w and h are also quoted as
//                                              3.16 W / 4.84 H by Landing.jsx)
//   ExtrudeGeometry  depth 1.55, bevelThickness 0.090, bevelSize 0.084,
//                    bevelSegments 5, curveSegments 4, flat normals
//   material         metalness 1, roughness 0.045, envMapIntensity 1.95,
//                    iridescence 0.06 / IOR 1.38 / range [100,560],
//                    clearcoat 0, colour white
//   buildEnvironment every box(), every glow(), the #101012 floor, the horizon,
//                    and STRIP_SCALE 0.81 - the strips' listed widths are
//                    pre-scale, so the key strip is 96 * 0.81 = 78 px
//   lights           key 0xffffff 0.30, rim 0xffffff 0.45
//   renderer         antialias false, ACES, toneMappingExposure 1.08
//   post chain       samples 4 desktop / 2 mobile or low-power -> RenderPass ->
//                    UnrealBloomPass(0.52,
//                    0.34, 0.88) -> lens uAmount 0.0011 -> GammaCorrectionShader
//   placement        resize()'s gate x/y/scale/yaw, draw()'s gate rotation and
//                    position
//
// What is NOT frozen is the contour field behind it - FIELD_VERT, FIELD_FRAG,
// their uniforms and the `field` mesh. That is the next piece of work and it is
// deliberately in the same file, so read the three constraints in FIELD_FRAG's
// closing comment before touching it: the sheet must stay the CSS page colour,
// the blend stays in sRGB, and the chain must keep ending in the gamma pass.

// The canvas edge is invisible only while the field is exactly --color-paper, so
// both colours are read from the page's own tokens rather than copied here. A
// second copy of a colour is a second thing to forget, and a skin that swaps the
// tokens repaints the canvas with them for free. The literals are The Orbit
// Sheet's, used only when the stylesheet has not parsed yet.
const FIELD_FALLBACK = 0x111112;
const RING_FALLBACK = 0xd2ff00;

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

// Two posts and a lintel, as one closed outline. `t` is the frame's own
// thickness - how wide a post is and how deep the lintel is - and it is the only
// control for it. 0.40 until 4 Sept 2026, raised on Sumeet's report that the
// frame had thinned. It had not: the outline is untouched since the glass build
// and a smaller bevel leaves *more* flat face, not less. What changed is that a
// chrome post carries a bright band down one side and a near-black face on the
// other, and the dark face reads as page rather than as post - so the lit part
// alone reads as the whole thickness. The fix he asked for is real thickness,
// not more light, because more light is what he had already capped.
// `w` and `h` are load-bearing beyond this file: Landing.jsx draws an SVG
// dimension chain quoting 3.16 W and 4.84 H, which are 2w and 2h. `t` is not
// quoted there, so it is the one of the three that can move alone.
function gateShape(w = 1.58, h = 2.42, t = 0.45) {
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
  // 1024x512. A mirror at roughness 0.045 samples close to the sharpest mip, so
  // the map is read almost directly and a coarse equirect turns a specular
  // streak into a staircase along the arris. The PMREM pass is a one-off at
  // mount.
  c.width = 1024;
  c.height = 512;
  const g = c.getContext('2d');
  // A near-black void with a few separated lights standing in it, and the black
  // between them is what matters rather than any one light's width. A flat
  // mirror face reflects one region of this map at once, so a *continuous*
  // bright area arrives on that face as a flat wash and the solid reads as
  // painted sheet metal. Measured 4 Sept 2026: a full-width white horizon band -
  // which is what a clear-glass room wants, because it draws the Fresnel rim -
  // put the posts at 235,235,235 across their whole front. Separated strips land
  // instead as the blown-out bands the reference images are made of. The bound
  // is 118 px, also measured: at that width they merge into one blob.
  // Not pure black, and the difference is the whole reason a face turned away
  // from every light still reads as metal. At #000000 a flat face pointing at
  // the void came back at 0 and the solid had holes in it - unlit patches
  // darker than the page they stand on, which is not what an unlit mirror does
  // in a real room. #101012 through envMapIntensity lands those faces just
  // above the page's own (17,17,18), so the silhouette stays readable and
  // nothing on the object is a hole. Measured 4 Sept 2026 over the gate's
  // bounding box at 1600x900: 4065 pixels darker than the page at #000000,
  // 0 at this value. Raising it much further is what turns the near-black body
  // into brushed aluminium; that failure is recorded above.
  g.fillStyle = '#101012';
  g.fillRect(0, 0, c.width, c.height);
  // A faint lift under the horizon, so the underside of the lintel is not pure
  // void. Above it stays at the base tone: an upward-facing face mirrors the
  // ceiling almost undistorted, and any more tone up there is a flat tone
  // painted across the whole top of the lintel.
  const floor = g.createLinearGradient(0, 256, 0, 512);
  floor.addColorStop(0.00, '#1a1a1c');
  floor.addColorStop(1.00, '#101012');
  g.fillStyle = floor;
  g.fillRect(0, 256, c.width, 256);

  // One softbox: bright in the middle, falling to the room at both edges. The
  // soft ends are what make the streak travel along an arris as the portal
  // sways instead of switching on and off at a hard boundary.
  // Every strip narrows by this much, about its own centre. Sumeet, 4 Sept 2026:
  // "i would like to reduce the reflection brightness from the 3d hero, just by
  // a little bit", and 0.81 is the value he picked on the running page.
  //
  // It is a scale rather than eleven rewritten widths so the numbers below stay
  // the readable record of what was tuned on 4 Sept, and so one edit moves all
  // of them together the way the reference set was designed.
  //
  // This is the only lever that reaches reflection brightness, and that is
  // measured rather than assumed: over the gate box at 1600x900, envMapIntensity
  // 1.95 -> 0.90 moved the bright area 0.0% and toneMappingExposure 1.08 -> 0.86
  // moved it 0.5%, because a pure white strip clips at any of those values. The
  // same sweep on width moved it 21%. Do not reach for the other two.
  const STRIP_SCALE = 0.81;
  const box = (x, y, w, h, peak) => {
    x = x + w * (1 - STRIP_SCALE) * 0.5;
    w = w * STRIP_SCALE;
    const grad = g.createLinearGradient(x, 0, x + w, 0);
    grad.addColorStop(0.00, '#000000');
    grad.addColorStop(0.50, peak);
    grad.addColorStop(1.00, '#000000');
    g.fillStyle = grad;
    g.fillRect(x, y, w, h);
  };

  // A soft round light, and this is what shapes a flat face. A strip lands on a
  // mirror face as a hard line with black either side; a radial falloff lands as
  // a sweep from bright to dark across the whole face, which is what both
  // reference images show and what a strip alone could not give.
  const glow = (cx, cy, r, peak) => {
    const grad = g.createRadialGradient(cx, cy, 0, cx, cy, r);
    grad.addColorStop(0.00, peak);
    // Steep. A gentle falloff spreads the same light over most of the map and
    // the room stops being a room; the faces then read as one flat tone, which
    // is brushed aluminium rather than the near-black bodies in the references.
    grad.addColorStop(0.32, peak.replace(')', ', 0.22)').replace('rgb', 'rgba'));
    grad.addColorStop(1.00, 'rgba(0,0,0,0)');
    g.fillStyle = grad;
    g.fillRect(cx - r, cy - r, r * 2, r * 2);
  };

  // Three, at azimuths a quarter-turn apart, so the solid is never turned to a
  // side that has no light on it at all. Measured 4 Sept 2026: with only the
  // strips below, the right-hand post sat at 6,6,7 against a 10,10,11 page and
  // the silhouette was invisible.
  // Raised and widened 4 Sept 2026 with the strips. These are what a flat face
  // sees when it is not pointed at a strip, so they are the difference between
  // a dark face and a black one - and "the black parts are outshining it" is
  // what Sumeet reported when they were half these values. They stay well under
  // the strips: only the pure white strips are meant to clear the bloom
  // threshold.
  glow(120, 250, 300, 'rgb(96,96,96)');
  glow(590, 236, 250, 'rgb(72,72,72)');
  glow(980, 268, 330, 'rgb(104,104,104)');
  // A dimmer one low and to the side, for the underside of the lintel.
  glow(760, 430, 230, 'rgb(54,54,54)');

  // A thin white line on the equator, across every azimuth. A ray leaving a
  // face at a grazing angle travels almost horizontally and so samples the
  // equator, so this is the one mark that reaches the whole silhouette rather
  // than one side of it - which is what the reference images have and what the
  // scattered lights alone could not give. Thin is the condition: a band this
  // tall (6 px of 512) reads on a flat front face as a single hairline, while
  // the 0.42-0.53 ramp tried first read as a wash over the entire face.
  const horizon = g.createLinearGradient(0, 240, 0, 274);
  horizon.addColorStop(0.00, 'rgba(255,255,255,0)');
  horizon.addColorStop(0.44, 'rgba(236,236,236,0.55)');
  horizon.addColorStop(0.50, 'rgba(255,255,255,1)');
  horizon.addColorStop(0.56, 'rgba(236,236,236,0.55)');
  horizon.addColorStop(1.00, 'rgba(255,255,255,0)');
  g.fillStyle = horizon;
  g.fillRect(0, 240, c.width, 34);

  // The key, and the tall streak that runs the length of a post. Wide, because
  // width here is how much of a face comes back blown rather than merely bright,
  // and blown is what the bloom pass has to work with. Black still separates
  // them: the failure mode is a continuous bright field, not a wide light.
  // Widened from 72 / 46 / 54 / 50 px on 4 Sept 2026, on Sumeet's instruction
  // that the solid was not silver enough and the black was outshining it. This
  // width is the only real brightness control in the scene, and that is a
  // measured claim rather than a preference: envMapIntensity was moved 1.95 to
  // 3.10 and toneMappingExposure 1.08 to 1.22 in the same session, and the two
  // together shifted the blown area by under 6%. A pure white strip clips at
  // either value, so what a face returns is set by how much of the map it sees.
  // 118 px is still the known failure - a solid white blob and a haze over the
  // page - so this sits below it.
  box(40, 20, 96, 450, '#ffffff');
  // Four more at scattered azimuths, so the solid catches a streak wherever it
  // is turned rather than only at one angle of sway.
  box(286, 60, 62, 380, '#ffffff');
  box(506, 0, 72, 512, '#ffffff');
  box(700, 100, 52, 320, '#ffffff');
  box(868, 30, 68, 440, '#ffffff');
  // A thin companion beside three of them. Every reference image carries a set
  // of close parallel highlights rather than one, which is what a real softbox
  // with a frame and a diffuser panel does.
  box(176, 90, 14, 300, '#ffffff');
  box(614, 40, 12, 380, '#ffffff');
  box(806, 130, 13, 260, '#ffffff');
  // Two short ones below the horizon. A mirror shows the floor as readily as the
  // ceiling, and with nothing down there the lower half of the solid went dead
  // black instead of catching the underside streak the references have.
  box(180, 300, 90, 150, '#f2f2f2');
  box(640, 330, 70, 130, '#f2f2f2');
  // A dim fill opposite the key, so the shadow side is not dead black. Neutral,
  // for the same reason the rim light is: anything with a hue in this room is a
  // hue on the metal.
  box(408, 210, 60, 90, '#9aa0a6');
  // No strip of the page's ink in here. One was tried on 4 Sept 2026 so the
  // coloured edge would belong to the palette, and it came back as a flat lime
  // panel across the whole top of the lintel: an upward-facing surface mirrors
  // whatever is above it almost undistorted, so a saturated block in the room
  // is a saturated block on the object. Rejected. The colour on the edges comes
  // from iridescence and the cool fill, which are angle-dependent and so stay
  // on the edges. Do not put a coloured shape back in this room.

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

// The animated field: topographic isolines drifting behind the gate, with a
// subtle magnetic hover and a clarity lift. Chosen live on 4 Sept 2026 from five
// candidates; the rest are recorded in DESIGN_PROGRESS.md so a later session does
// not re-offer one he has already turned down.
//
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

  // One anti-aliased hairline per whole step of c. fwidth holds the stroke at a
  // constant pixel width however fast c moves across the screen, which is what
  // stops the radial and isoline families aliasing into moire where they crowd.
  float hair(float c) {
    float d = abs(fract(c) - 0.5);
    float w = max(fwidth(c), 0.0008) * 1.25;
    return 1.0 - smoothstep(0.0, w, d);
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

    // Gentle magnetic pull: the pattern leans toward the cursor rather than
    // anything being drawn at the cursor. Shared by all five patterns.
    vec2 pull = -toMouse * hoverCore * 0.14;
    vec2 pWarped = p + pull;

    // Localized harmonic resonance: subtle wave ripples along lines near pointer
    float hoverResonance = sin(distToMouse * 3.6 - uTime * 3.2) * hoverCore * 0.12;

    float t = uTime * 0.62;

    // Topographic isolines: a height field contoured at a fixed interval, so the
    // lines close into loops and drift in place rather than scrolling past. The
    // four sine terms are deliberately at unrelated frequencies - a common
    // factor makes the field repeat and the loops start to tile.
    //
    // Chosen by Sumeet 4 Sept 2026 from five served live at ?bg=0..4. The four
    // he did not pick - the shipped streamlines, a drifting weave, rays from the
    // gate and ledger columns - are in DESIGN_PROGRESS.md with their
    // measurements, and so are the three he had already rejected earlier that
    // day: a PCB trace bus, a coordinate dot matrix with a cursor reticle, and
    // the Orbit Sheet's concentric rings.
    float height = sin(pWarped.x * 0.42 + t * 0.25) * cos(pWarped.y * 0.38 - t * 0.20)
                 + 0.55 * sin(pWarped.x * 0.24 + pWarped.y * 0.31 + t * 0.16)
                 + 0.35 * cos(pWarped.x * 0.61 - pWarped.y * 0.17 - t * 0.22)
                 + hoverResonance;

    // 0.30 is the contour interval. Smaller packs the loops tighter; the hair()
    // width is in screen pixels, so tightening it past about 0.20 puts adjacent
    // contours inside one stroke and the field greys over instead of drawing.
    float lineCoord = height / 0.30;
    float distToLine = abs(fract(lineCoord) - 0.5);
    float lw = max(fwidth(lineCoord), 0.0008) * 1.25;
    float lineShape = 1.0 - smoothstep(0.0, lw, distToLine);

    // Every fifth contour slightly heavier, the way a survey map indexes them.
    float lineWeight = mix(0.75, 1.15, step(0.5, 1.0 - abs(fract(lineCoord * 0.20) - 0.5) * 4.0));

    // Soft traveling kinetic pulse along the line: subtle, not blinding
    float pulse = exp(-pow(fract((pWarped.x * 0.10 - t * 0.35 + floor(lineCoord) * 0.33)) - 0.5, 2.0) * 220.0);
    float travelPulse = pulse * lineShape * 0.28;

    // Spatial falloff: natural fade away from gate so outer corners remain pure paper
    float gDist = gateDist(p);
    float falloff = exp(-gDist * 0.055);
    float glassZone = exp(-gDist * 0.30);

    // REFINED VISIBILITY (brighter, legible, clean, minimal):
    float baseLine = lineShape * lineWeight * 0.205 * falloff;
    float glassLines = glassZone * lineShape * 0.225;

    // Delicate micro-packets and coordinate ticks strictly along hovered lines
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
    // The line colour, and it is the brightness control rather than the amount:
    // once a hairline is fully drawn the blend lands exactly on this value and
    // stops, so raising the pattern amount cannot lift it. Measured 4 Sept 2026:
    // 8x on the amount moved p99 55 -> 59, four steps, while moving this mix
    // moved it 33 -> 48.
    //
    // Half the slate, half the page's own lime accent, at 0.58. Chosen by Sumeet
    // on the running page from seven candidates: "a mix of look 2 & 3, and with
    // just a little teeny tiny low brightness". Slate alone and lime alone were
    // both offered and both passed over.
    //
    // The ground is untouched by this: it is uPaper wherever the pattern is
    // zero, so a brighter line raises contrast and never greys the black
    // between the lines. Measured over a far corner across all seven: 51.0%
    // exact page colour, identical.
    vec3 slate = vec3(0.56, 0.64, 0.70);
    vec3 restLineCol = mix(uPaper, isDark > 0.5 ? mix(slate, uInk, 0.50) : uInk, isDark > 0.5 ? 0.58 : 0.26);
    // Active hover line color: refined clear ink
    vec3 hoverLineCol = mix(restLineCol, uInk, 0.58);

    vec3 lineColor = mix(restLineCol, hoverLineCol, clamp(hoverField * 0.85 + pulse * 0.45, 0.0, 1.0));

    // The blend runs in sRGB, where a line drawn at half strength lands at half
    // the perceived distance between sheet and ink. Blending the same stop in
    // linear light puts it well above that - a visibly brighter, fatter line for
    // the same maths - so the two steps stay apart: mix here, convert after.
    // Tried and reverted 4 Sept 2026: writing the line as alpha over a
    // transparent canvas moves that blend into the composer's linear target and
    // makes the field the loudest thing in the frame.
    vec3 col = mix(uPaper, lineColor, clamp(totalPattern * totalMask, 0.0, 1.0));

    // Then hand the pipeline true light. The composer's target is linear, so
    // colorspace_fragment below is a no-op and the sRGB encode happens once, in
    // the gamma pass at the end of the chain.
    gl_FragColor = sRGBTransferEOTF(vec4(col, 1.0));

    // No tonemapping_fragment beside it, deliberately, and this is what keeps
    // the sheet the exact CSS colour through a post-processing chain: the ACES
    // curve is compiled into the lit material and never runs over this plane.
    // The chain therefore ends in a gamma pass and not an OutputPass, which
    // would tone map the whole frame and take the sheet to about 0.21 of itself.
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
      // Off, and it is not a downgrade: since the post chain arrived the scene
      // is drawn into the composer's own target and never into this buffer, so
      // MSAA asked for here was allocated and then never sampled. The composer
      // target below owns anti-aliasing when the device has the headroom.
      antialias: false,
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

  const gl = renderer.getContext();
  const debug = gl.getExtension('WEBGL_debug_renderer_info');
  const mobile = navigator.userAgentData?.mobile
    ?? /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
  const lowPower = isLowPowerHero({
    gpu: gl.getParameter(debug?.UNMASKED_RENDERER_WEBGL || gl.RENDERER),
    deviceMemory: navigator.deviceMemory,
    hardwareConcurrency: navigator.hardwareConcurrency,
  });
  // Desktop devices use up to 4x MSAA, mobile tops out at 2x, and
  // constrained devices avoid work that bloom softens away in the final image.
  const pixelRatio = Math.min(window.devicePixelRatio, lowPower ? 1.25 : 1.75);
  renderer.setPixelRatio(pixelRatio);
  renderer.setClearColor(FIELD, 1);
  // ACES filmic, and it reaches the lit material only. The contour field is a
  // ShaderMaterial with no tonemapping_fragment include, so the sheet keeps the
  // exact colour the CSS page uses and only the glass is mapped. That is what
  // lets a specular run past 1.0 and roll off as a highlight instead of
  // clipping to a flat white patch along the arris.
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  // Above 1.0 on purpose: the reference images are near-black bodies with the
  // streaks blown fully out, and at 1.0 the hottest reflection of a pure white
  // softbox lands around 0.8 and reads as light grey. Like envMapIntensity
  // below, it barely moves the blown area; it is kept above 1.0 for the roll-off
  // and is not the dial to reach for when the solid looks wrong.
  renderer.toneMappingExposure = 1.08;
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
    // Rounded, not chamfered, and this is the single change that makes the
    // solid read as chrome. One segment gives an edge one flat facet, which
    // reflects one region of the room and so draws one thin line - correct for
    // cast glass, which is what this was built for. Every chrome reference
    // Sumeet supplied carries a *stack* of parallel highlights along each edge,
    // and that is what a turned corner does: each of these five facets faces a
    // slightly different way, catches a different strip, and the set of them
    // reads as one bright band wrapping the form.
    // The size stays under the old chamfer's: it was opened to 0.16 / 0.15
    // first, cut to 0.105 / 0.098 on "the edges are too rounded, so make it less
    // round", and cut again to here on "a very little less ... a little bit more
    // sharper". The segment count is what buys the chrome, not the radius, so
    // the radius can keep coming down without losing the effect - the five lines
    // just sit closer together and read as a crisper edge.
    bevelThickness: 0.090,
    bevelSize: 0.084,
    bevelSegments: 5,
    curveSegments: 4,
  });
  // Per-face normals. Shared vertices average the facets back into a curve,
  // which is exactly the plaster look; splitting them keeps every facet flat -
  // including across the rounded edge above, where the discrete steps are the
  // parallel highlight lines and a smoothed normal would blur them into one.
  gateGeometry.computeVertexNormals();

  const gate = new THREE.Mesh(
    gateGeometry,
    new THREE.MeshPhysicalMaterial({
      // Silver, not tinted. On a metal this colour multiplies the reflection, so
      // anything but white lays a cast over the whole room.
      color: 0xffffff,
      metalness: 1.0,
      // A mirror, but not a perfect one. At exactly 0 the solid reflects the
      // environment canvas sharply enough to show its own seams; a little
      // roughness blurs the map without softening the streaks.
      roughness: 0.045,
      // Well over 1, and it is the room being black that makes that safe. This
      // multiplies every sample, so a black wall stays black at any value while
      // a pure white strip goes past 1.0 - which is the only way an 8-bit canvas
      // environment can produce a highlight bright enough to clear the bloom
      // threshold. At 1.0 nothing did, and the solid had crisp streaks and no
      // light coming off them; the references are made of that light.
      // This number is close to inert, and that is worth knowing before anyone
      // reaches for it. Measured 4 Sept 2026: moving it 1.95 to 3.10 changed the
      // gate's blown area by under 1%, because ACES puts a pure white strip past
      // the shoulder at either value. It is the floor of the room, not this,
      // that decides how dark the unlit faces are, and the strip widths that
      // decide how bright the lit ones are.
      envMapIntensity: 1.95,
      // A thin-film coat over the metal, and what puts the faint rainbow on the
      // chamfers instead of a flat silver line. Dispersion cannot do that job
      // here: dispersion colours what is seen through a solid, and nothing is
      // seen through this one any more.
      iridescence: 0.06,
      iridescenceIOR: 1.38,
      // Wide, so the fringe cycles through cyan, magenta and warm across a
      // single chamfer instead of sitting on one hue.
      iridescenceThicknessRange: [100, 560],
      // No lacquer. Clearcoat is a second mirror over a surface that is already
      // a mirror, and it only flattens the contrast between face and chamfer.
      clearcoat: 0.0,
      clearcoatRoughness: 0.0,
    })
  );
  gate.geometry.center();
  scene.add(gate);

  const key = new THREE.DirectionalLight(0xffffff, 0.30);
  key.position.set(3.2, 4.4, 5.5);
  scene.add(key);
  // Behind, so it hits only the faces turned away from the camera - the grazing
  // ones - and its whole contribution lands on the silhouette and the chamfers.
  // White, not cyan. A metal takes the light's colour into its specular whole,
  // with no diffuse term to dilute it, so the cyan lamp this used to be painted
  // a blue edge down the right-hand post. Silver needs a neutral room.
  const rim = new THREE.DirectionalLight(0xffffff, 0.45);
  rim.position.set(-4.5, -1.4, -3.2);
  scene.add(rim);

  // The chain that makes it look photographed rather than rendered. Every
  // reference image is a chrome body whose highlights bleed light into the
  // black around them and carry a colour fringe on the edges; neither is a
  // material property, so no amount of tuning the metal produces either.
  // On capable GPUs this is the only anti-aliasing in the scene. An
  // EffectComposer with no target of its own builds an unsampled one, so from
  // the moment this chain was added the renderer's own `antialias` flag was
  // being allocated and thrown away - every chamfer arris and every contour
  // hairline was drawn with no coverage information at all, which is the
  // stair-stepping visible standing still and crawling while the gate sways.
  // HalfFloatType is the composer's own default and is kept explicitly: the
  // bloom pass reads values well past 1.0 off the blown strips, and an 8-bit
  // target clips them to flat white before the pass ever sees them.
  const composerTarget = new THREE.WebGLRenderTarget(1, 1, {
    type: THREE.HalfFloatType,
    samples: heroSamples({ lowPower, mobile, maxSamples: gl.getParameter(gl.MAX_SAMPLES) }),
  });
  const composer = new EffectComposer(renderer, composerTarget);
  composer.addPass(new RenderPass(scene, camera));

  // Threshold above the body and below the hot streaks, so only the blown parts
  // spill. Lower and the whole solid glows, which is fog rather than light.
  // Strength, radius, threshold. The threshold is the important one: at 0.62
  // the contour lines cleared it and the whole background glowed, which reads as
  // fog. Raised to 0.88 on 4 Sept 2026 with the wider strips: the solid now has
  // roughly seven times the blown area it had, so the old threshold let far more
  // of it spill.
  // Radius is the number that decides whether this is light coming off the
  // solid or a fog over the page. At 1.0 the haze reached the copy column and
  // lifted the sheet by eye across the whole right half - which is the one thing
  // this pass is not allowed to do. Strength and radius both came down on
  // 4 Sept 2026, 0.80/0.42 to 0.52/0.34, for the same reason the threshold went
  // up - the same pass over a much brighter solid is a much bigger haze.
  // Measured after: the page ground is exact at 4 of 6 sample points and lifts
  // to 21 and 18 at the two nearest the gate. That residue is the price of the
  // brightness Sumeet chose on 4 Sept and is not a regression to chase.
  const bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.52, 0.34, 0.88);
  composer.addPass(bloom);

  // Chromatic aberration and a wide horizontal flare, in one pass. The offset
  // scales with distance from the centre, the way a real lens fails, and is
  // near zero over the middle of the frame where the copy sits.
  const lens = new ShaderPass({
    uniforms: {
      tDiffuse: { value: null },
      uAmount: { value: 0.0011 },
      uAspect: { value: 1 },
    },
    vertexShader: /* glsl */ `
      varying vec2 vUv;
      void main() {
        vUv = uv;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: /* glsl */ `
      precision highp float;
      varying vec2 vUv;
      uniform sampler2D tDiffuse;
      uniform float uAmount;
      uniform float uAspect;
      void main() {
        vec2 d = vUv - 0.5;
        float r = length(vec2(d.x * uAspect, d.y));
        // Gated on how bright this pixel already is, so the split lands on the
        // chrome and never on the sheet or its contour lines. Without the gate a
        // 1 px hairline splits into a red line and a cyan line, which changes
        // the background - and the background is not what this pass is for.
        vec3 here = texture2D(tDiffuse, vUv).rgb;
        float lum = dot(here, vec3(0.2126, 0.7152, 0.0722));
        vec2 off = d * uAmount * r * 4.0 * smoothstep(0.30, 0.75, lum);
        // Red pushed out, blue pulled in, green left alone: the split a simple
        // lens makes, and the red-and-cyan edge fringe in the references.
        vec4 c;
        c.r = texture2D(tDiffuse, vUv + off).r;
        c.g = texture2D(tDiffuse, vUv).g;
        c.b = texture2D(tDiffuse, vUv - off).b;
        c.a = texture2D(tDiffuse, vUv).a;
        gl_FragColor = c;
      }
    `,
  });
  composer.addPass(lens);

  // Last: the sRGB encode, and nothing else. Tone mapping is already done, per
  // material, inside the RenderPass - which is the whole reason this is not an
  // OutputPass. See the note at the end of FIELD_FRAG.
  composer.addPass(new ShaderPass(GammaCorrectionShader));

  // Half the visible height at a given distance, for both the fitting below and
  // the plane that has to cover the frame exactly.
  const halfAt = d => Math.tan((camera.fov * Math.PI) / 360) * d;
  let gateBaseY = 0;
  let gateBaseX = 0;
  let gateBaseYaw = 0;
  // Where the portal sits in the same -1..1 space the pointer is reported in,
  // so the draw loop can ask how near the cursor is to it without projecting
  // anything. Kept in sync with gateBaseX/gateBaseY by resize().
  let gateAimX = 0;
  let gateAimY = 0;

  function resize() {
    const { clientWidth: w, clientHeight: h } = host;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    composer.setSize(w, h);
    composer.setPixelRatio(pixelRatio);
    lens.uniforms.uAspect.value = w / h;
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
    // above the void, so the portal moves down into the void below it.
    gateBaseY = wide ? 0 : -halfH * 0.41;
    // And it shrinks there. Clear glass could stand behind the paragraph because
    // it was transparent; a mirror cannot. Measured 4 Sept 2026 at 390x844, a
    // full-size chrome post ran at 200+ luminance straight under the white body
    // copy. At 0.72 the solid clears the copy block and stops being a contrast
    // problem, and it also stops being clipped by the top of the canvas, which
    // it already was.
    gate.scale.setScalar(wide ? 1 : 0.72);
    // And it is turned, on narrow only. Wide, the portal sits off to one side and
    // the camera already sees it at an angle; centred on a phone it faces the
    // lens square, so every front face reflects the one part of the room that is
    // directly behind the camera - which is black. Measured 4 Sept 2026 at
    // 390x844: the solid rendered as a flat 8,8,9 slab with no highlight on it
    // at all. A third of a radian is enough to bring a softbox onto the posts.
    gateBaseYaw = wide ? 0 : 0.38;
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
  let cachedScrollY = window.scrollY;

  const hostTop = () => cachedHostRect.top - (window.scrollY - cachedScrollY);

  function onPointer(e) {
    const top = hostTop();
    const inside =
      e.clientX >= cachedHostRect.left &&
      e.clientX <= cachedHostRect.left + cachedHostRect.width &&
      e.clientY >= top &&
      e.clientY <= top + cachedHostRect.height;
    hoverTarget = inside ? 1 : 0;
    aim.set(
      ((e.clientX - cachedHostRect.left) / cachedHostRect.width) * 2 - 1,
      ((e.clientY - top) / cachedHostRect.height) * 2 - 1,
    );
  }

  function onPointerLeave() {
    hoverTarget = 0;
  }

  function onScroll() {
    scroll = Math.max(-1, Math.min(1, -hostTop() / window.innerHeight));
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
    const t = clock.elapsedTime;
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

    gate.rotation.y = gateBaseYaw + Math.sin(t * 0.30) * 0.045 + eased.x * 0.155 * reach;
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

    composer.render();
  }

  function tick() {
    if (!visible || disposed) return;
    draw();
    frame = requestAnimationFrame(tick);
  }

  const ro = new ResizeObserver(() => {
    resize();
    cachedHostRect = host.getBoundingClientRect();
    cachedScrollY = window.scrollY;
    onScroll();
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
    composer.dispose();
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
