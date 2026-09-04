# reserve-gate

**An AI agent's spending limit should not be a sentence in a prompt. It should be
a balance that runs out.**

## Start here

Shops are beginning to let AI agents buy things on their own. To do that, the
shop hands the agent a payment key. That key has no limit on it. The agent can
spend everything, any number of times, forever.

Today the only thing stopping it is an instruction written in English, somewhere
in the agent's prompt: *"do not spend more than ten thousand rupees."* That is
not a control. It is a request. A model can misread it, a bad product name can
talk it out of it, and a bug can loop it a thousand times.

`reserve-gate` sits between the agent and Razorpay and turns that request into a
balance. Money is set aside once. Every purchase is checked against what is left.
When it runs out, it runs out — and the agent is told exactly which rule stopped
it and how much remains.

**The model still decides what to buy. It never decides what it is allowed to
spend.**

## Does it actually stop anything?

This is the whole project in one table. The gate holds 16 separate guards. We
deleted all 16 and replayed the same 150 attack cases through it.

| | Money calls that got through | Money moved |
|---|---:|---:|
| All 16 guards **deleted** | **37** | **₹68,502** |
| Guards **on** | **0** | **₹0** |

`[measured 1 Sept 2026: python harness/gate_off.py, exit 0]`

Of the 150 cases, 130 try to move money and 80 of those should be refused. With
every guard deleted, 37 got through. The other 43 were still caught by code the
16 deletions do not cover — the run names them rather than claiming credit.

The script exits non-zero if the "guards on" row is ever dirty, so it cannot
report a win by being broken.

## What it is, in one picture

Think of a prepaid card you top up once and hand to someone else.

- They can spend it many times. (Rule R3)
- They cannot spend more than is on it. (R1)
- It stops working on a date you choose. (R2)
- You can cancel it right now, mid-purchase. (R4)
- No single swipe can be larger than a limit you set. (R5)
- It cannot be swiped 1,000 times in a minute. (R6)
- A double-swipe of the same purchase is charged once. (R7)
- Anything above your comfort line waits for your approval. (HOLD)

Every one of those is a real rule in code, not a description. Each one is
enforced by a plain function with no model inside it, so the same input always
gives the same answer, and the answer can be explained.

## Try it yourself, no signup

Open <https://reserve-gate.onrender.com>. You get your own spending block. You
can shop with it, try to overspend it, revoke it mid-session, paste an attack
into a product name, delete one of the gate's own rules and watch the score
change, and pay a real ₹100 Razorpay test-mode order with a test card.

No key, no account, no real money. First load takes about 32 seconds while the
free server wakes up.

## What Razorpay already ships, and where the gap is

Razorpay shipped Agent Studio on 12 March 2026. Its published principles are
bounded, gated, audited and revocable: *"Every agent operates within boundaries
the merchant defines"*, *"No agent takes an irreversible action without explicit
merchant approval"*, *"every agent action passes through Razorpay's
platform-level validation layer before execution"*, *"Every single action is
logged with a full audit trail"*, and *"The merchant can turn off any agent at
any time. One tap. Immediate."*
[vendor-doc, fetched 1 Sept 2026: razorpay.com/blog/razorpay-agent-studio-principles-guardrails-and-merchant-control]

Those controls govern the agents **Razorpay runs**. They are platform-side, and
they bound a first-party agent inside Razorpay's own product.

Nothing bounds the agents **anyone else runs**. The hosted MCP server at
`mcp.razorpay.com` exposes `create_order`, `capture_payment`, `update_payment`,
`create_payment_link`, `create_payment_link_upi`, `send_payment_link` and
`create_qr_code` to any client holding a merchant key. That surface carries no
spending cap, no expiry, no revocation and no policy audit trail. A merchant who
hands an outside AI buyer a key hands it the whole account.

`reserve-gate` is that missing piece: the same four properties, applied to the
third-party path, by a service the merchant runs. Agent Studio secures Razorpay's
agents; this secures everyone else's.

## Run it on your own machine — three commands

```console
pip install -r requirements.txt
pytest
python -m src.buyer --scripted --overspend
```

These commands need no credential and no network. The last command starts the
buyer and gate over local stdio using a fresh temporary ledger, then shows six
policy refusals.

The same buyer can be driven by a real model instead of a fixed basket. That
mode is the demo, never the measurement: a number taken through a model is not
repeatable.

```console
pip install -r requirements-llm.txt
export GEMINI_API_KEY=...            # and RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET
python -m src.buyer --llm
```

Nothing loads `.env` for you; export the variables yourself. Set `GEMINI_MODEL`
if the default id has retired — a model can still appear in `ListModels` after
it stops answering for new keys.

To regenerate the measured artifacts:

```console
python harness/run_eval.py
python harness/mutate.py
```

## The demo site

<https://reserve-gate.onrender.com> opens with a plain-language landing page.
`/demo` then walks one purchase through limits, an AI shopping trip, the gate's
decisions, and payment proof. The attack, mutation, trace, rules, and evidence
pages remain under **Technical proof**. Sandbox decisions are genuine
`decide()` calls against real SQLite on the same `BEGIN IMMEDIATE` path and land
in the same hash-chained log; they never call Razorpay.

The final checkpoint can create one fixed ₹100 Razorpay test-mode order and
capture its returned payment through the same authorization, reservation,
upstream, and settlement functions used by MCP. It accepts no amount, tool,
receipt, target order, or upstream URL from the browser. A separate 24-hour
cookie permits one order attempt per browser, and an atomic SQLite reservation
caps the whole server at 20 attempts per UTC day. The route refuses production
keys. Without test credentials or capacity it keeps the sandbox usable and
links to the committed recorded payment trace.

Each visitor gets their own block, keyed by an `HttpOnly; SameSite=Lax` cookie.
The `caller_id` is derived from that cookie server-side and never read from a
request field, so one visitor cannot name another's block, order or idempotency
key. The pages and their JSON API are the only unauthenticated surface;
`/mcp`, `/approve`, `/revoke`, `/unfreeze` and `/block` are unchanged and still
require their bearer tokens. The admin prefixes are matched *before* the public
list, so a page path can never widen into an admin route.

The pages are a React application built with Vite and Tailwind CSS; the source
is in `frontend/` and the build writes exactly two files, `web/app.js` and
`web/app.css`, over the two filenames `src/dashboard.py` already routes. Both are
**committed**, so Render's build command stays `pip install` and the
three-command repro below needs no npm. Rebuild after changing anything under
`frontend/`:

```
cd frontend && npm install && npm run build
```

Restart the server afterwards — `dashboard.static()` is `lru_cache`d, so a
running process keeps serving the previous bytes.

The rule-deletion page runs `harness/mutate.py` for real, **in a subprocess**.
The mutation is selected by index into that module's fixed list — a
caller-supplied string there would be remote code execution — and each run
writes to a throwaway audit file, so the committed chain is untouched. The child
process matters as much as the index: scoring a mutation rebinds `ledger.decide`
for a whole interpreter, so doing it in the server would delete a policy guard
from every concurrent money decision for as long as the run takes, on a route
that needs no token. The swap now lives and dies in a process that serves
nothing.

**First request may take about 32 seconds while the free instance wakes.**
Measured 30 August 2026 with `curl -w "%{time_starttransfer}"` against a
naturally spun-down instance; warm is 0.29–0.35 s. Reload once.

Response budgets are a gate, not a claim. `python harness/perf_check.py` starts
a local server, measures p95 over 12 samples per endpoint, prints the table and
exits non-zero on a miss, so the reported line and the exit status come from one
expression and cannot disagree. Run 31 August 2026 on one Windows laptop:
**20 of 20 within budget, exit 0**. The tightest rows are `POST /api/shop` at
91.7 ms of a 100 ms budget and `POST /api/mutate` at 689.4 ms of 2000 ms; both
move by a factor of two or more between runs depending on what else the machine
is doing, which is why the check is a threshold and not a published latency.

API rows have the round-trip floor subtracted — `/health` p95, measured in the
same run with the same client, 16.2 ms here. `/health` runs none of this code,
so whatever it costs is the harness measuring itself, and the budgets are
written about the server's own work. The six page rows keep the floor, because a
browser pays it too. The floor has its own ceiling of 60 ms, so a globally slow
run fails outright instead of quietly shrinking every excess.

`web/rules.json` carries the same eight rules as the table below, for the rules
page to render. They are two copies of one fact: change either and change both.

## Architecture

```text
AI buyer / MCP client
        |
        | Bearer token
        v
  reserve-gate -------- POST /webhook <---- Razorpay
        |                 raw-body HMAC
        | ALLOW
        v
Razorpay hosted MCP

SQLite owns the block, reservations, event dedupe, idempotency and freeze state.
Every decision and reconciliation outcome is appended to a hash-chained JSONL log.
```

Only four upstream tools exist at the gate: `create_order`, `capture_payment`,
`fetch_order`, and `fetch_payment`. The handwritten declarations are the
allowlist; a new Razorpay money tool is unavailable until explicitly reviewed.
See [ARCHITECTURE.md](ARCHITECTURE.md) for the trust boundaries and money flow.

## The eight policy rules

These are the prepaid-card properties above, written out exactly. Each is a
separate check. A call must pass all of them; failing any one is a refusal that
names the rule. The demo site counts nine, because it lists the approval gate
below the table as a check of its own.

| Rule | Control |
|---|---|
| R0 | Amount is an integer in the supported sub-unit range and currency matches the block. |
| R1 | Committed plus held money cannot exceed the reserved block. |
| R2 | The block expires at a fixed instant, no later than 90 days. |
| R3 | One block supports multiple debits; captures require this gate's reservation. |
| R4 | Revocation refuses every later money call immediately. |
| R5 | One transaction cannot exceed the configured per-call cap. |
| R6 | A rolling velocity limit counts allowed and refused money calls. |
| R7 | Idempotency replays the first result and rejects a key reused with different parameters. |

Calls above the approval threshold become `HOLD`. Only the operator token can
approve, revoke, inspect, or unfreeze a block; the buyer token cannot reach
those controls.

**R7 has a boundary a caller inherits by omitting the key.** No money call is
left unkeyed: when a client sends no `idempotency_key`, the gate derives one from
the caller, the tool, and the stable money fields, and remembers it for five
minutes. `receipt` and `notes` are deliberately excluded, because a model
regenerating a call after a timeout rewords them, and a key that moved with that
prose would mint a second real order for one purchase. The cost of that choice is
the other direction: two genuinely different purchases of the same amount, by the
same caller, within five minutes, collapse into one — the second replays the
first. A client that asserts the two are different says so by supplying its own
key, which never expires and binds the full arguments, so reusing it with a
changed amount, tool, or payee is a conflict rather than a replay. `src/buyer.py`
supplies one on every call.

## Measured adversarial evaluation

[eval_report.md](eval_report.md) is generated from 150 labelled cases using the
real policy and SQLite ledger, without credentials or network access.

| Class | Cases | False-allow | Wrong effect/rule |
|---|---:|---:|---:|
| Prompt injection | 40 | 0 | 0 |
| Block cap and expiry | 30 | 0 | 0 |
| Idempotency and replay | 25 | 0 | 0 |
| Revocation and runaway loops | 25 | 0 | 0 |
| Webhook reconciliation | 20 | 0 | 0 |
| Negative controls | 10 | 0 | 0 |
| **Total** | **150** | **0** | **0** |

Confusion matrix: 58/58 expected allows, 87/87 expected blocks, and 5/5
expected holds matched. Webhook cases additionally assert the observable effect:
`APPLY`, `NOOP`, or `REJECT`.

Passing cases alone can be weak. `harness/mutate.py` removes each of the 16
policy guards in turn and re-scores all 150 cases. Every removal is detected.
[harness/provenance.md](harness/provenance.md) maps each attack class to its
external basis and states where evidence is still limited.

[audit_sample.jsonl](audit_sample.jsonl) is the other half of the evidence: not a
simulation but a real run against Razorpay test mode. Six orders placed through
the gate with their real order ids, a seventh refused by the block cap, and one
of them paid with a test card and captured back through `capture_payment`, which
moved 10,000 paise from held to spent and closed with a `debit_committed` record.
The block ends at reserved 1,000,000, spent 10,000, held 895,000. Its hash chain
verifies from the first record, whose parent is null.

Chain tail digest:
`698448f71af0422efd15d5ce41167a817a2d484ee2ce3ccc10e2f072cc589856`. The chain is
unkeyed, so on its own it catches an edited, deleted or reordered record but not
a wholesale rewrite. This digest is committed here, beside the log it summarises,
so a recomputed log no longer matches the value the repository already holds.

### Timestamped independently

Both digests above are committed by the author, so they date the evidence only as
well as git history does. [eval_report.md.ots](eval_report.md.ots) is an
[OpenTimestamps](https://opentimestamps.org) proof that closes that gap: it
commits `eval_report.md` to the Bitcoin blockchain through public calendar
servers, so the date the results existed is one nobody in this project can move.

```console
pip install opentimestamps-client
ots verify eval_report.md.ots          # run it beside eval_report.md
```

[audit_sample.jsonl](audit_sample.jsonl) is stamped the same way, so the
settlement record and the evaluation are both anchored:

```console
ots verify audit_sample.jsonl.ots
```

Stamped 4 Sept 2026 over sha256
`065cbdfa98acc053a313118c89aff4a3bdc3ab286359a534c1fd06e8eac2afcf` and
`851db90523dad116f8eb1389ef9c084e9d5da3becc97fe1846ad4e516efbb10b`. **The Bitcoin
attestation is still pending** — it attaches within about a day, and until then
`ots verify` reports *pending confirmation* and exits non-zero. Run
`ots upgrade eval_report.md.ots` to pull the block in once it lands. What the
command must never report is that the file does not match: `eval_report.md` is
marked `-text` in `.gitattributes` so its bytes are identical on every platform,
and regenerating the report without re-stamping it would break the proof.

## Webhook reconciliation

`POST /webhook` is the only non-health route exempt from bearer authentication. It is
authenticated separately with `X-Razorpay-Signature`, using HMAC-SHA256 over
the exact raw request bytes and a dedicated `RAZORPAY_WEBHOOK_SECRET`.
`x-razorpay-event-id` is mandatory and atomically deduplicated in SQLite.

| Input | Response and effect |
|---|---|
| Missing or invalid signature | `401`; body is not parsed and money state is untouched. |
| Missing event ID or malformed signed JSON | `400`; no money state change. |
| Missing server-side webhook secret | `503`. |
| Body larger than 1 MB | `413`; rejected before parsing. |
| Valid `payment.captured` matching a held reservation | `200`; held becomes spent exactly once. |
| Duplicate, unsupported, out-of-order, or unknown-order event | `200`; audited no-op. |
| Late capture or payment/amount/currency/status conflict | `200`; rejected reconciliation and the block freezes. |
| Unexpected SQLite failure | Generic `500`; transaction rolled back. |

The normal capture response and webhook use the same reservation transaction.
Whichever commits first wins; only the same payment on the same reservation is a
no-op. Reusing one payment across reservations freezes the block, and an in-flight
idempotency result is completed so retries return the captured result.

A frozen block fails closed under G4. An operator can acknowledge a reviewed
conflict with `POST /unfreeze/{block_id}` using `RESERVE_GATE_ADMIN_TOKEN`; this
only clears the freeze flag. It does not choose a payment record or rebuild the
ledger from Razorpay history, which remains future work.

### Live webhook proof

`[measured 29 Aug 2026: Razorpay test checkout, dashboard capture, Render logs,
and two signed HTTP replays]` Order `order_TVVWyByMSNDYa3` reserved 10,000
paise: the deployed block read `spent=0`, `held=10000`. Capturing payment
`pay_TVVZMykxzzrcva` produced event `TVVfkSQLpXzape`; the webhook changed the
block once to `spent=10000`, `held=0`, `available=990000`. Two further signed
requests reused that event ID. Both returned `accepted=true`, `applied=false`,
`reason=duplicate_event`; Render logged one `capture_applied` and two
`duplicate_event` no-ops. Razorpay's dashboard did not expose the original body,
so the replays used a minimal valid signed JSON body with the identical event ID.

## Security controls

- Bearer authentication uses constant-time comparison; operator controls use a
  different token from the buyer.
- Webhooks use raw-body HMAC and constant-time comparison before JSON parsing.
- Caller authorization headers are stripped. Only server-side Razorpay
  credentials can reach upstream.
- SQLite uses parameterized statements, WAL, a busy timeout, `BEGIN IMMEDIATE`,
  and `CHECK (spent + held <= reserved)`.
- Event IDs and idempotency keys are deduplicated atomically.
- Upstream timeouts retain their hold because the payment outcome is unknown;
  only a proven refusal releases money.
- Free text such as notes and receipts never enters the deterministic decision.
- Secrets are lazy-loaded from environment variables and redacted from audit
  records and webhook stdout summaries.

## Audit-chain scope

Each JSONL record includes the previous record's SHA-256 digest. This detects an
edited, deleted, or reordered line. The chain is intentionally unkeyed, so an
attacker who can rewrite the entire file can recompute it. The generated tail
digest is committed in `eval_report.md` beside `harness/audit_run.jsonl`; a
wholesale rewrite then disagrees with git history.

The honest question is what that actually stops, so here is the answer by
attacker rather than by adding an HMAC:

| Attacker | Can they forge the log? | Does it matter? |
|---|---|---|
| The AI agent | No. It speaks MCP and never touches the file. | This is the attacker the gate exists for, and the chain is more than enough for it. |
| A shell on the deployed instance | Yes | They also hold `RAZORPAY_KEY_SECRET`, so they can skip the gate and move money directly. Forging the log is a harder route to something they already have. |
| The author | Yes, but not silently | No key fixes this, because the author would hold the key too. Two things do. Reproducibility: a reader can run `harness/mutate.py` from a clean clone, with no credential and no network, and regenerate every number in `eval_report.md`. And the OpenTimestamps proof above, which is the part git alone could not give — a rewritten log needs a rewritten tail digest, that digest lives in `eval_report.md`, and re-stamping it produces a proof **dated after** the original. The author cannot manufacture an earlier one. |

An HMAC was considered and rejected. Its key would live beside the process
writing the log, where an attacker who can rewrite the file already has it, and a
`verify()` that needs a secret is one a reader with a clone cannot run — which is
the only reason the log exists. Reproducible beats keyed — and since 4 Sept 2026
the log is timestamped as well, which is the one thing reproducibility does not
supply on its own: a date nobody in this project can move.

The audit covers requests that reach application code. Schema-invalid MCP
arguments can be rejected by the transport before a tool runs, so they have no
application audit line. The committed harness remains reproducible from a clean
clone and does not rely on the deployed log.

## Connect an MCP desktop client

The tested path is the pinned stdio-to-HTTP bridge:

```json
{
  "mcpServers": {
    "reserve_gate": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote@0.6.0",
        "https://<render-url>/mcp",
        "--header",
        "Authorization:${AUTH_HEADER}"
      ],
      "env": { "AUTH_HEADER": "Bearer <reserve-gate-token>" }
    }
  }
}
```

Keep `0.6.0` pinned and keep no space after `Authorization:` in the header
argument. The hosted connector UI is not an alternative: it accepts OAuth
client credentials, while `reserve-gate` intentionally uses a static bearer
token and does not implement an OAuth authorization server.

## Honest limitations

- Razorpay test mode does not expose Reserve Pay itself. This project implements
  the spending-block semantics around Orders and Payments.
- Render's free service has ephemeral disk. A redeploy or cold start loses the
  SQLite block, dedupe ledger, and live audit log, recreating a full block. The
  server writes `COLD_START_LEDGER_RESET`, but the deployed URL cannot enforce a
  durable cap across that reset. Evaluation numbers come from persistent local runs.

  The two obvious repairs were both checked and neither is available here. A free
  Render web service cannot attach a persistent disk at all, so the storage fix
  does not exist on this plan. Render's free Postgres expires 30 days after it is
  created, which would place its deletion inside the review window — a database
  that removes itself during judging is a worse failure than a stated limit,
  because it fails silently and later. The durable store is therefore git: the
  authoritative run happens locally and `eval_report.md` and `audit_sample.jsonl`
  are committed, where a reader can re-verify the hash chain against a value the
  repository already holds.
- Pending approvals live in process memory. A restart forgets the approval link;
  the SQLite reservation still expires safely.
- The hash chain does not stop a process owner from rewriting the whole log, and
  no unkeyed chain can. What it does and does not cover is set out by attacker
  under *Audit-chain scope*. **Reduced 4 Sept 2026, not eliminated:** the tail
  digest now sits inside an OpenTimestamps-proofed file, so a rewrite can only be
  published under a proof dated later than the original. That closes backdating.
  It does not prove the log was never rewritten *before* it was stamped.
- A caller that omits `idempotency_key` inherits a five-minute collapse between
  two identical-amount purchases. See the R7 boundary above.
- Manual unfreeze does not reconcile or rebuild payment history.
- The demo site's blocks are per-cookie and live on that same ephemeral disk, so
  a visitor's block is erased when the instance sleeps — including mid-session if
  they leave the tab for fifteen minutes. Clearing the cookie also abandons the
  old block rather than deleting it.
- The demo site's model box is rate-limited by a per-day counter in SQLite, which
  the same spin-down resets. With no `GEMINI_API_KEY` set, or once the budget is
  spent, it serves a recorded run committed at `web/recorded_llm_run.json` and
  says on the page that it is doing so.
- The public test-checkout cap is also stored on Render's ephemeral disk. A
  sleep or redeploy resets its daily counter; this is an abuse brake for a demo,
  not durable billing infrastructure.
- The keepalive workflow depends on GitHub's scheduler firing at least once a
  day, and nothing here can make it land. The old cron asked for 90 events a day
  and GitHub delivered about 5% of them (measured 30 August 2026), which against
  a 15-minute spin-down is the same as no workflow. It now asks for hourly
  attempts and whichever one lands loops until a fixed UTC hour, so a single
  landing covers an afternoon; `gh run list --workflow=keepalive.yml` showed
  about four landings a day over 1-3 September 2026. That is a mitigation, not a
  guarantee. Warm the URL by hand before anything that matters.
- Dependency and policy tests are deterministic; the final dashboard webhook
  replay is the separate live integration proof.
- **Requests are not cryptographically signed.** A caller is identified by its
  bearer token, and each block is bound server-side to the caller derived from
  it. Signed mandates would additionally prove *who originated* a request. Six
  days went into the permission question — how much, for how long, revocable when
  — and a signature answers origin, not permission. The two are complementary and
  only one was built.
- **A block cannot be split into delegated sub-budgets.** One agent holds one
  block. An agent cannot hand a smaller, weaker allowance to a sub-agent without
  contacting this server. Doing that properly needs a capability-token layer with
  offline attenuation and a delegation depth limit; a half-built version of that
  is worse than not having it, because it would look like a boundary while
  leaking one.

Copy `.env.example` to `.env` only when using Razorpay test mode. Never commit it.
