# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

The primary visitor is a **Razorpay AI Buildathon panellist** on the Track 01
shortlist call. They arrive cold, from a submission form, with a few minutes and
several other projects behind them. Their job is to decide whether this is real
engineering or a demo video. They try to break it first, then look for evidence.

They are technical, they know payments, and they are unusually well placed to catch
an overstated claim: Razorpay shipped Agent Studio on 12 March 2026 with the same
four words the judging bar uses. A panellist from that team arrives already asking
"we built this".

## Product Purpose

`reserve-gate` is a policy gate that sits between an AI buyer agent and Razorpay's
hosted MCP money tools. The agent proposes a purchase; the gate decides ALLOW, HOLD
or BLOCK before any money moves, and writes one hash-chained audit record per
decision.

Success for the site is that a judge who tries to break the gate cannot, and then
finds the numbers behind that claim checkable without leaving the page.

## Positioning

Razorpay's Agent Studio governs the agents **Razorpay runs**. Nothing governs the
agents anyone else runs through `mcp.razorpay.com`, which ships seven money tools
with no cap, no expiry, no revocation and no policy audit. That gap is the product.

The decision path contains **no model**. The LLM proposes; a deterministic rule
engine disposes. This is what makes "false-allow = 0" a property claim refutable by
one counterexample, rather than an accuracy score over a self-written test set.

## Operating Context

The site is one deployed FastMCP app on Render's free tier serving both `/mcp` and
six demo pages. A judge browses it in a desktop browser, usually cold — the free
instance sleeps after 15 idle minutes and a measured cold start is 32.2 s. The pages
drive the real gate against a real SQLite ledger, scoped to the visitor's own block
through an `HttpOnly` cookie.

Six routes: `/` landing, `/demo` guided walkthrough, `/attack` adversarial bench,
`/mutate` live guard-deletion re-score, `/trace` payment record, `/rules` the control
table, `/evidence` the reports.

## Capabilities and Constraints

- Eight policy rules R0–R7 plus an approval gate; nineteen security rules G1–G19.
- Money primitives are Orders and Payments. Refunds and payment links are out of
  scope — the hosted MCP server disables the first and caps the second.
- The deployed ledger does **not** survive a redeploy or a spin-down. Render's free
  tier has no persistent disk. The authoritative numbers come from a local run and
  are committed to git.
- `reserve-gate` **models** NPCI Reserve Pay semantics. It does not use Reserve Pay;
  that API is not exposed in test mode. This distinction must never be blurred.
- Front end is React 19 + Vite 7 + Tailwind v4 in `frontend/`, building to committed
  `web/app.js` and `web/app.css`. It is deliberately not a Python dependency.
- The 3D hero is procedural three.js — no mesh file, no HDR, no texture request.

## Brand Commitments

Binding, confirmed by the user on 1 Sept 2026:

- **Every word of site copy is fixed.** The redesign replaces the visual world only.
- The name `reserve-gate` and the hexagon wordmark stay.
- The Razorpay logo chip stays on the pages that carry it.
- The empty OpenTimestamps proof slot on `/evidence` stays, and stays empty until
  after the merge.
- Four reference images at `references/` are pinned as design directions in their
  own right, not as a mood board to average.

## Evidence on Hand

Real, in the repo, and checkable:

- `eval_report.md` — confusion matrix over 150 cases, false-allow 0.
- `harness/mutation_report.md` — every guard deleted in turn, every deletion noticed.
- `harness/gate_off.py` — measured 1 Sept 2026: all sixteen guards off lets **37
  unauthorised calls and ₹68,502** through, against 0 and ₹0 with them on.
- `harness/multi_model.json` — six models, six identical BLOCK / R5 verdicts.
- `audit_sample.jsonl` — 16 hash-chained records with one real settlement.
- `harness/provenance.md` — every attack class cited to an outside source.

**Absences that must never be fabricated:** there are no customers, no testimonials,
no pricing, no uptime figure, and no OpenTimestamps proof yet.

## Product Principles

1. **The gate has no model in it.** Every claim of determinism has to survive a
   judge reading the decision path.
2. **A claim ships with the artefact that proves it**, joined by a run id. A green
   badge is not evidence.
3. **Fail closed.** Any error, timeout or unparseable argument is a refusal.
4. **State the limits out loud.** The deployed ledger is wiped on spin-down; the
   product models Reserve Pay rather than using it. Naming a gap outranks hiding it.
5. **A judge who tries to break it and cannot is convinced.** Interaction beats
   assertion, so the site is a test bench first and a pitch second.

## Accessibility & Inclusion

Reduced motion is respected — the hero holds one still frame. The 3D scene is
`aria-hidden` because every word it illustrates is in the headline beside it. A
WebGL refusal is not a failure state: the page is complete without the canvas.
