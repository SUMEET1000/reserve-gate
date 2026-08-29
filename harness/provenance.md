# Provenance — where every attack class comes from

A test set written by the person being tested proves very little. This file
exists so each of the 150 cases in `cases.jsonl` can be traced to something
outside this repository, and so the places where that trace is weak are named
rather than hidden.

Evidence tags follow one rule: anything about a system outside this repo carries
its provenance in the sentence. `[vendor-doc]` was read in their documentation.
`[measured]` was run here, with the command. `[assumed]` is a working belief with
the experiment that would settle it named. Untagged reads as assumed.

## Attack classes

| Cases | Class | External basis | Tag |
|---|---|---|---|
| 40 | `prompt_injection` | OWASP GenAI **LLM01:2026 Prompt Injection**, published 4 Aug 2026 | `[vendor-doc: genai.owasp.org 2026 list]` |
| 30 | `block_cap_expiry` | NPCI UPI Reserve Pay: issuer maximum block ₹10,000, 90-day validity. Razorpay API: `amount` is "the smallest currency sub-unit", minimum 100 | `[vendor-doc: NPCI circular coverage; razorpay.com/docs]` |
| 25 | `idempotency_replay` | Razorpay webhook docs: duplicate delivery is "expected behaviour"; ordering "may not be followed at all times" | `[vendor-doc: razorpay.com/docs/webhooks/validate-test]` |
| 25 | `revocation_runaway` | Razorpay agentic-payments blog, 20 Feb 2026: users "retain full visibility, control, and the ability to revoke consent instantly". OWASP GenAI **LLM06:2026 Unbounded Consumption** (denial of wallet) | `[vendor-doc: razorpay.com/blog/agentic-payments-and-npci; genai.owasp.org]` |
| 20 | `webhook_reconciliation` | Razorpay webhook documentation: HMAC-SHA256 over the raw body, duplicate delivery is expected, dedupe uses `x-razorpay-event-id`, and delivery order is not guaranteed | `[vendor-doc: razorpay.com/docs/webhooks/validate-test]` |
| 10 | `negative_control` | None, deliberately. These are the calls that **must** pass. A gate that refuses everything scores a perfect false-allow and is worthless; these are what stop that reading | — |

The threat the whole project is a control for is OWASP GenAI **LLM03:2026
Excessive Agency**, which climbed three places in the 2026 list because model
outputs now autonomously invoke external APIs. `[vendor-doc: genai.owasp.org]`

## The injection payloads, and an honest limit

The forty injection cases use ten payload shapes: instruction override,
system-prompt spoofing, role injection, two classic SQL forms, template
expression injection, structured-data confusion (duplicate JSON keys), a null
byte, ANSI terminal escapes, and tool-call spoofing.

These are the standard published families for LLM01 and they appear across the
public prompt-injection corpora. **They were not fetched from a named corpus on
28 Aug 2026, because the machine that would have fetched them had no budget for
it that day.** `[assumed: verify each shape against genai.owasp.org's LLM01:2026
page and one public payload corpus before submission, and replace this paragraph
with the citation]`

That is the weakest line in this file, and it is stated plainly rather than
dressed up. What the payloads are *not* is invented to be easy: the claim each
one tests is not "this payload is refused" but "this payload changed nothing",
which is checked mechanically — see below.

## What the harness measures, and what it cannot

**Twin checking.** An injection case names the clean call it shadows via
`twin_of`. The claim is not that a payload is refused. It is that the decision is
byte-identical with the payload present and absent, because `decide()` never
reads free text. A twin that diverges is a failure, in either direction.

**`notes` and `receipt` are proved structurally, not by cases.**
`policy.Call` has no field for either, so a product name or an address cannot
reach `decide()` at all. `tests/test_policy.py` asserts that directly. One
assertion about the type is a stronger claim than twenty-five cases about
values, and it cannot rot the way a case list can.

**Malformed arguments never reach the policy on the MCP path.** FastMCP
validates against the tool signature with pydantic before the function body runs,
so `True` arrives as `1` and `"50000"` as `50000` — genuine ints, every one.
`1000.7` and `null` are refused by the schema and leave **no audit record at
all**. `[measured 28 Aug 2026: calling mcp.call_tool in-process]` The harness
calls the ledger directly, so it *can* reach `decide()` with those values, and
the R0 type cases do. The split is deliberate — the schema judges shape, the
policy judges money — but it means the audit trail's coverage has a documented
edge, and the README says so.

## The bias problem, and what actually answers it

The bypass cases were built alongside the code. A cited source for the *class*
does not make the *case* independent, so provenance alone cannot establish that
the suite would notice a broken control.

Three separate things fix three separate problems, and only two of them are
about who wrote what:

1. **"The cases were written to pass."** Answered by `harness/mutate.py`, not by
   provenance. It deletes each rule from a copy of `decide()` and re-scores the
   same 150 cases; every rule has to be noticed. That control does not care who
   wrote the cases, which is the point — asking the author whether the tests are
   weak is circular, and removing the thing under test is not.
2. **"The payloads were invented here."** Partly answered above, and honestly
   flagged as unfinished.
3. **"A whole attack class is missing."** Neither provenance nor mutation
   testing can prove completeness. This remains an explicit limit.

## The audit chain's limit

`harness/audit_run.jsonl` is hash-chained and the chain is **unkeyed**. It catches an edited,
deleted or reordered record. It does not catch a wholesale rewrite, because whoever can do that
can recompute every digest — `tests/test_audit.py` asserts exactly this, and asserts that the
published tail digest catches what `verify()` cannot.

What closes it is the anchor, not the chain: the tail digest is published in `eval_report.md` and
both files are committed, so git holds a value a forged log no longer matches. An HMAC was
considered and rejected — the key would live beside the process writing the log, and a `verify()`
that needs a secret is one a reader with a clone cannot run, which is the only reason the log is
here.
