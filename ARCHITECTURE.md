# Architecture

## The problem

Razorpay's hosted MCP server exposes 35+ tools to any client holding a test key,
including `create_order`, `capture_payment`, `create_payment_link` and
`create_qr_code`. There is no spending limit, no expiry, no revocation and no
record of what an agent tried to do. A merchant cannot hand that surface to an
AI buyer.

`reserve-gate` is the thing in between.

## Shape

```
  buyer agent (in this repo)              Claude Desktop
      --scripted | --llm                       |
              \                                | npx mcp-remote  (stdio -> HTTP)
               \                               |
                v                              v
        +--------------------------------------------------+
        |  reserve-gate                                     |
        |                                                   |
        |  HTTP transport:  Authorization: Bearer <token>    |
        |                   caller header stripped here      |
        |                                                   |
        |  four tools, declared by hand:                     |
        |    create_order      -> POLICY                     |
        |    capture_payment   -> POLICY                     |
        |    fetch_order       -> read, logged               |
        |    fetch_payment     -> read, logged               |
        |                                                   |
        |  POLICY  ALLOW -> forward upstream                 |
        |          BLOCK -> tool result, isError: true       |
        |          HOLD  -> awaits POST /approve/{call_id}   |
        |                                                   |
        |  the block:  born at boot from policy.yaml         |
        |              killed by POST /revoke/{block_id}     |
        |                                                   |
        |  /approve, /revoke and /block take the ADMIN       |
        |  token, which the agent is never given             |
        |                                                   |
        |  every outcome -> one line in audit.jsonl          |
        +--------------------------------------------------+
                        |
                        |  MCP, Authorization: Basic <key:secret>
                        v
              https://mcp.razorpay.com/mcp
                        |
                        v
              Razorpay test mode: Orders + Payments
```

## Why the tool list is written by hand

The four tools above are declared as four functions. `reserve-gate` does not
mirror Razorpay's tool list and filter it.

That declaration *is* the allowlist. A Razorpay tool that is not written here
cannot be reached through this server, including any tool Razorpay adds after
this was written. Refunds, payment links, QR codes and settlements are all
absent by construction rather than by a rule that could be misconfigured.

The cost is honest: if Razorpay renames an argument, the code is edited by hand.
For a control whose whole claim is that money actions are bounded, silently
adopting a changed money API would be the wrong default.

## Trust boundaries

| Boundary | Control |
|---|---|
| Public internet to the deployed server | Bearer token, constant-time compare. `/health` is the only open route. |
| Agent to the operator's controls | A second secret. `/approve`, `/revoke` and `/block` take `RESERVE_GATE_ADMIN_TOKEN`; the agent holds only `RESERVE_GATE_TOKEN`. Guarding the approval gate with the token the gated party already carries would let the agent approve its own spending. |
| Caller to upstream | The caller's `Authorization` header is removed before forwarding. Only the gate's own Razorpay credential ever reaches Razorpay. |
| Upstream data to the model | Order notes, receipts and error strings come back from Razorpay and reach the model's context. No decision is ever made from that text. |
| Upstream URL | A module constant. It is never read from a request. |

## Money primitive

Orders and Payments, not Payment Links (test mode caps those at 30 per business)
and not refunds (`create_refund` is disabled on Razorpay's hosted MCP server).

The block is debited **once**, as a reservation at `create_order`, keyed by the
returned `order_id`. `capture_payment` for a known order commits that reservation
and never debits again. An unpaid reservation is released when it times out, so
orders that are never paid do not permanently consume the block.

`capture_payment` is handed only a `payment_id`, so the gate reads the real
`order_id`, `amount` and `currency` off Razorpay's payment object rather than
trusting arguments the caller chose.

A block holds one currency, and a call in any other currency is refused rather
than converted. Razorpay settles foreign cards, so this is not an India-only
gate; it is that comparing two currencies needs a live exchange rate inside the
decision, and a decision resting on a number that moves every second is not
explainable.

## What this models, and does not

The spending block models NPCI's UPI Reserve Pay ("Single Block Multiple
Debits"): one authorization, many debits until exhausted, expired or revoked.
Reserve Pay itself is not exposed in Razorpay's test-mode API, so `reserve-gate`
implements those semantics rather than calling them.

`reserve-gate` bounds the **agent's** access, not the merchant's key. Anyone
holding the raw Razorpay key can call `mcp.razorpay.com` directly and bypass the
gate. The control assumes the agent is given a `reserve-gate` token and never the
underlying credential.
