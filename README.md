# reserve-gate

`reserve-gate` lets an AI buyer use Razorpay without receiving an unlimited
payment surface. It places a deterministic spending block between the buyer and
Razorpay, explains every refusal, and records every outcome.

The model is never trusted to police itself. It proposes calls; this service
decides whether money may move.

## Three-command reproduction

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

## The seven policy rules

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
- The hash chain does not stop a process owner from rewriting the whole log.
- Manual unfreeze does not reconcile or rebuild payment history.
- Dependency and policy tests are deterministic; the final dashboard webhook
  replay is the separate live integration proof.

Copy `.env.example` to `.env` only when using Razorpay test mode. Never commit it.
