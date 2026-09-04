"""The public demo surface: a judge attacks the gate with their own hands.

Everything a panel would ask for lives in files nobody opens - eval_report.md,
the mutation table, audit_sample.jsonl. This serves them, and more importantly
gives a visitor their own real spending block so they can try to break it. A
judge who watches a demo is impressed; one who tries to break it and cannot is
convinced.

Two structural rules hold the whole module together:

**Python emits JSON and never markup.** `web/` is plain HTML, CSS and JS served
as opaque bytes. The seam means a later design pass can replace every file in
`web/` - or drop in a framework - without touching a line of Python.

**A visitor's block is real.** Same SQLite file, same `BEGIN IMMEDIATE`, same
`decide()`, same hash chain as the agent's. The caller id is derived server-side
from an HttpOnly cookie, never from a request field (§11.0 item 2), so a cookie
authorises only its own demo block - never a rule, a tool, or another block. The
`demo-session-` namespace cannot collide with the agent's caller id.

Only the fixed, capped live-checkout routes call Razorpay. The sandbox routes
remain local and repeatable.
"""
import asyncio
import contextlib
import dataclasses
import functools
import hashlib
import hmac
import json
import os
import pathlib
import re
import secrets
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

from starlette.responses import JSONResponse, PlainTextResponse, Response

from . import audit, ledger, webhook
from .policy import ALLOW, HOLD, Call, Config, State, decide, load_config

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

COOKIE = "rg_demo"
LIVE_COOKIE = "rg_live"
# The visitor's caller id is sha256("demo-session-" + token). The prefix is what
# guarantees a demo block can never be the agent's, whose token is a credential.
SESSION_NS = "demo-session-"
SESSION_TOKEN = re.compile(r"\A[A-Za-z0-9_-]{16,64}\Z")

# Static files, by exact name. There is no caller-supplied path anywhere in this
# module, so directory traversal is not defended against - it is absent.
PAGES = {
    "/": "index.html", "/demo": "demo.html", "/attack": "attack.html", "/mutate": "mutate.html",
    "/trace": "trace.html", "/rules": "rules.html", "/evidence": "evidence.html",
    "/app.css": "app.css", "/app.js": "app.js", "/razorpay-logo.png": "razorpay-logo.png",
    # Two icons and no more. The SVG is what every current browser takes, and
    # the 64px PNG is the alternate for the ones that will not read an SVG icon.
    # A 32px copy and a second 64px copy that was byte-identical to favicon.png
    # were deleted on 4 Sept 2026: nothing linked either, and a routed file that
    # no page asks for is one a later session assumes is load-bearing.
    "/favicon.svg": "favicon.svg", "/favicon.png": "favicon.png",
    # The hero's three.js chunk, loaded by / alone. Named here rather than
    # matched by prefix, so the allowlist stays closed; vite.config.js pins the
    # filename to match.
    "/app-HeroScene.js": "app-HeroScene.js",
    # The self-hosted faces, latin subset, named one by one because this
    # table is a closed allowlist and a glob here would be a directory to walk.
    # Self-hosted rather than fetched from Google's CDN: one fewer host to reach
    # on a cold start the free tier already pays 32 s for, and the page keeps
    # working with no outbound request at all.
    **{f"/fonts/{f}": f"fonts/{f}" for f in (
        "Cormorant-normal-500.woff2",
        "MonaSans-normal-400.woff2", "MonaSans-normal-700.woff2",
        "AzeretMono-normal-400.woff2", "AzeretMono-normal-500.woff2",
        "AzeretMono-normal-700.woff2",
        "BarlowCondensed-normal-700.woff2",
        "BigShouldersStencil-normal-800.woff2",
    )},
}
MEDIA = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".png": "image/png",
         ".webp": "image/webp", ".woff2": "font/woff2", ".svg": "image/svg+xml"}

# One audit record is a few hundred bytes, so this is a few hundred decisions -
# far more than a visitor makes. Reading the tail rather than the file is what
# keeps a 2-second poll from becoming the thing that stalls a free instance.
FEED_TAIL_BYTES = 64_000

# Whitelists, not deletions. An audit record carries caller_id, block_id,
# reservation_id, order_id and upstream ids; a response is assembled field by
# field so a field added to the log later cannot leak by default.
FEED_FIELDS = ("ts", "event", "kind", "tool", "rule", "reason", "amount",
               "currency", "receipt", "hash", "prev_hash")
DETAIL_FIELDS = ("amount", "available", "reserved", "spent", "held", "max_txn",
                 "approval_over", "count", "expected", "currency", "available_after",
                 "reserved_amount", "expires_at", "revoked_at", "frozen_at",
                 "call_id", "idem_key", "replay", "order_id")

MAX_BODY = 64_000

# Deliverable #2: the committed record of a real settlement. /trace reads it and
# /evidence verifies its chain, so the name is written once.
SAMPLE = "audit_sample.jsonl"


# ---------------------------------------------------------------- artefacts

@functools.lru_cache(maxsize=1)
def default_config() -> Config:
    """Read once, on first use rather than at boot.

    Parsing the artefacts inside the ASGI lifespan would delay the moment the
    port accepts connections, and a cold start on this tier is 32 s already
    (measured 30 Aug 2026). The first request pays ~20 ms once instead.
    """
    return load_config(os.environ.get("RESERVE_GATE_POLICY", str(ROOT / "policy.yaml")))


@functools.lru_cache(maxsize=None)
def artefact(name: str) -> str:
    """One committed file, read once per process. Every caller passes a literal."""
    try:
        return (ROOT / name).read_text(encoding="utf-8")
    except OSError:
        return ""


def model_rows() -> list | None:
    """The six-model table from `python -m harness.multi_model`, or None.

    It is read from the file the harness writes rather than copied into `web/`,
    so the page and the run cannot hold two versions of one measurement. A
    missing or unreadable file is None and the page shows its empty state: this
    is a report, and it must never 500 the host that moves money.
    """
    try:
        rows = json.loads((ROOT / "harness" / "multi_model.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rows if isinstance(rows, list) else None


# A detached OpenTimestamps proof opens with a fixed 31-byte magic, a version
# varint, the file-hash op (0x08 is sha256) and then the 32-byte digest it
# commits to. Reading those bytes is the whole parse; everything after them is
# the attestation path, which only `ots verify` needs.
OTS_MAGIC = b"\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94"
OTS_SHA256 = 0x08


def ots_proof() -> dict | None:
    """The OpenTimestamps proof of eval_report.md, checked rather than quoted.

    The report's digest is recomputed here on every read and compared against
    the digest the proof actually commits to, so a report regenerated without
    being re-stamped reports as not matching instead of the page printing a
    hash that no longer proves anything. Missing or unparseable is None and the
    panel shows its empty state, the same way model_rows() does: this is a
    report, and it must never 500 the host that moves money.

    `eval_report.md` is read as bytes, not through artefact(): a proof binds one
    exact byte string, and `.gitattributes` marks the file `-text` so the bytes
    are the same on every machine (edge case e9).
    """
    try:
        report = (ROOT / "eval_report.md").read_bytes()
        proof = (ROOT / "eval_report.md.ots").read_bytes()
    except OSError:
        return None
    head = len(OTS_MAGIC)
    if not proof.startswith(OTS_MAGIC) or len(proof) < head + 34 or proof[head + 1] != OTS_SHA256:
        return None
    stamped = proof[head + 2:head + 34].hex()
    digest = hashlib.sha256(report).hexdigest()
    return {"digest": digest, "stamped": stamped, "matches": digest == stamped,
            "proof_bytes": len(proof)}


@functools.lru_cache(maxsize=None)
def static(name: str) -> tuple[bytes, str]:
    """A file from web/ with its ETag. Missing is empty, not an exception: a
    half-deployed site should show a blank page, never a 500 on the money host."""
    try:
        body = (WEB / name).read_bytes()
    except OSError:
        body = b""
    return body, '"%s"' % hashlib.sha256(body).hexdigest()[:16]


@functools.lru_cache(maxsize=1)
def catalogue() -> dict:
    return json.loads(artefact("web/catalogue.json") or "{}")


def _res_of(rec: dict) -> str | None:
    detail = rec.get("detail") if isinstance(rec.get("detail"), dict) else {}
    return rec.get("reservation_id") or detail.get("reservation_id")


@functools.lru_cache(maxsize=1)
def sample_chain() -> dict:
    """audit_sample.jsonl, indexed for /trace, plus its verify() result.

    verify() re-reads and re-hashes the file, so it is cached here rather than
    re-run on a request path.
    """
    records, purchases, by_order = [], {}, {}
    for line in artefact(SAMPLE).splitlines():
        if line.strip():
            with contextlib.suppress(ValueError):
                records.append(json.loads(line))
    for r in records:
        if r.get("event") == "reservation_bound" and r.get("order_id"):
            by_order[r["reservation_id"]] = r["order_id"]
    for r in records:
        order = by_order.get(_res_of(r))
        if not order:
            continue
        p = purchases.setdefault(order, {"order_id": order, "amount": None,
                                         "receipt": None, "settled": False})
        if r.get("event") == "allow" and r.get("tool") == "create_order":
            p["amount"], p["receipt"] = r.get("amount"), r.get("receipt")
        if r.get("event") == "debit_committed":
            p["settled"] = True
    ok, line = audit.verify(str(ROOT / SAMPLE))
    return {"records": records, "by_order": by_order,
            "purchases": list(purchases.values()),
            "verified": ok, "bad_line": line,
            "tail": audit.tail_hash(str(ROOT / SAMPLE))}


# ------------------------------------------------------------------ session

# token -> the limits this visitor chose. In memory on purpose: a demo block
# does not survive a Render spin-down either (§11.0b), so persisting the config
# that describes it would outlive the thing it configures.
_SESSIONS: dict[str, Config] = {}
# call_id -> (token, Ref) for a HOLD this visitor took. On the demo the visitor
# is the operator of their own block, so they can release it; the agent's G12
# separation is untouched because nothing here reaches src.server._HOLDS.
_HOLDS: dict[str, tuple[str, ledger.Ref]] = {}
# token -> the last event id this visitor's webhook panel sent, so "send it
# again" replays a real id rather than minting a new one.
_LAST_EVENT: dict[str, str] = {}
# The webhook panel signs with this instead of RAZORPAY_WEBHOOK_SECRET: the demo
# proves the HMAC path, and Razorpay's real signing secret has no business being
# reachable from a public page.
_DEMO_SECRET = secrets.token_urlsafe(32)
LIVE_BROWSER_CAP = 3
LIVE_DAILY_CAP = 60
LIVE_AMOUNT = 10_000
_LIVE_AVAILABLE = _LIVE_ORDER = _LIVE_CAPTURE = None


def session_token(request) -> str:
    token = request.cookies.get(COOKIE) or ""
    return token if SESSION_TOKEN.match(token) else secrets.token_urlsafe(16)


def caller_of(token: str) -> str:
    return ledger.caller_id_for(SESSION_NS + token)


def config_of(token: str) -> Config:
    return _SESSIONS.get(token) or default_config()


def reply(payload, token: str, request, status: int = 200) -> Response:
    r = JSONResponse(payload, status)
    r.set_cookie(COOKIE, token, max_age=86400, path="/", httponly=True,
                 samesite="lax", secure=request.url.scheme == "https")
    return r


def live_token(request) -> str:
    token = request.cookies.get(LIVE_COOKIE) or ""
    return token if SESSION_TOKEN.match(token) else secrets.token_urlsafe(16)


def live_visitor(token: str) -> str:
    return ledger.caller_id_for("live-checkout-" + token)


def live_reply(payload, token: str, demo_token: str, request, status: int = 200) -> Response:
    r = reply(payload, demo_token, request, status)
    r.set_cookie(LIVE_COOKIE, token, max_age=86400, path="/", httponly=True,
                 samesite="lax", secure=request.url.scheme == "https")
    return r


def reserve_live_slot(conn, visitor: str, caller: str) -> tuple[bool, str, int | None]:
    """Atomically claim a browser and public Razorpay attempt."""
    moment = ledger.now_utc()
    day, now = moment.date().isoformat(), ledger.iso(moment)
    cutoff = ledger.iso(moment - timedelta(hours=24))
    conn.execute("BEGIN IMMEDIATE")
    try:
        used = conn.execute("SELECT COUNT(*) c FROM live_checkout_slots"
                            " WHERE visitor_id = ? AND attempted_at > ?",
                            (visitor, cutoff)).fetchone()["c"]
        if used >= LIVE_BROWSER_CAP:
            reason = f"This browser has used its {LIVE_BROWSER_CAP} live checkouts in 24 hours"
            conn.execute("ROLLBACK")
            return False, reason, None
        if conn.execute("SELECT COUNT(*) c FROM live_checkout_slots WHERE day = ?",
                        (day,)).fetchone()["c"] >= LIVE_DAILY_CAP:
            conn.execute("ROLLBACK")
            return False, f"Today's {LIVE_DAILY_CAP} public test checkouts have been used", None
        slot = conn.execute("INSERT INTO live_checkout_slots"
                            " (visitor_id, caller_id, day, attempted_at, status)"
                            " VALUES (?, ?, ?, ?, 'pending')", (visitor, caller, day, now)).lastrowid
        conn.execute("COMMIT")
        return True, "", slot
    except Exception:
        conn.execute("ROLLBACK")
        raise


async def api_live_order(request):
    token, demo_token = live_token(request), session_token(request)
    body = await body_of(request)
    if body:
        return live_reply({"error": "This checkout has a fixed item and amount"},
                          token, demo_token, request, 400)
    ready, reason = _LIVE_AVAILABLE() if _LIVE_AVAILABLE else (False, "Live checkout unavailable")
    if not ready:
        return live_reply({"error": reason, "recorded_proof": True},
                          token, demo_token, request, 409)
    visitor, caller, cfg = live_visitor(token), caller_of(demo_token), config_of(demo_token)
    conn = ledger.connect()
    try:
        ok, reason, slot = reserve_live_slot(conn, visitor, caller)
    finally:
        conn.close()
    if not ok:
        return live_reply({"error": reason, "recorded_proof": True},
                          token, demo_token, request, 429)
    try:
        order = await _LIVE_ORDER(caller, cfg)
        order_id = order["id"]
        if not isinstance(order_id, str) or not order_id.startswith("order_"):
            raise ValueError("Razorpay returned an invalid order")
        conn = ledger.connect()
        try:
            conn.execute("UPDATE live_checkout_slots SET status = 'created', order_id = ?"
                         " WHERE id = ? AND status = 'pending'", (order_id, slot))
        finally:
            conn.close()
    except Exception:
        conn = ledger.connect()
        try:
            conn.execute("UPDATE live_checkout_slots SET status = 'failed'"
                         " WHERE id = ? AND status = 'pending'", (slot,))
        finally:
            conn.close()
        return live_reply({"error": "Razorpay could not create the test order",
                           "recorded_proof": True}, token, demo_token, request, 502)
    return live_reply({"key_id": os.environ["RAZORPAY_KEY_ID"], "order_id": order_id,
                       "amount": LIVE_AMOUNT, "currency": "INR",
                       "display_item": "reserve-gate verification purchase"},
                      token, demo_token, request)


async def api_live_capture(request):
    token, demo_token = live_token(request), session_token(request)
    body = await body_of(request)
    if set(body) != {"payment_id"} or not isinstance(body.get("payment_id"), str):
        return live_reply({"error": "Send only the payment_id returned by Checkout"},
                          token, demo_token, request, 400)
    visitor = live_visitor(token)
    conn = ledger.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT id, caller_id, order_id FROM live_checkout_slots"
                           " WHERE visitor_id = ? AND status = 'created'"
                           " ORDER BY id DESC LIMIT 1", (visitor,)).fetchone()
        if row:
            conn.execute("UPDATE live_checkout_slots SET status = 'capturing'"
                         " WHERE id = ?", (row["id"],))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    if not row:
        return live_reply({"error": "No live order from this browser is ready to capture"},
                          token, demo_token, request, 404)
    try:
        result = await _LIVE_CAPTURE(row["caller_id"], config_of(demo_token),
                                     body["payment_id"], row["order_id"])
    except Exception:
        return live_reply({"error": "The payment could not be safely captured",
                           "recorded_proof": True}, token, demo_token, request, 502)
    conn = ledger.connect()
    try:
        conn.execute("UPDATE live_checkout_slots SET status = 'captured' WHERE id = ?",
                     (row["id"],))
        block = block_json(conn, row["caller_id"])
    finally:
        conn.close()
    return live_reply({"captured": True, "payment_id": result.get("id"), "block": block},
                      token, demo_token, request)


async def body_of(request) -> dict:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return {}
    try:
        parsed = json.loads(raw or b"{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


# --------------------------------------------------------------- the feed

def read_tail(offset: int | None) -> tuple[list[dict], int]:
    """Audit records appended since `offset`, and the new offset.

    Byte offsets, not line numbers: a poll then reads only what was appended,
    which is nothing at all in the common case. A partial trailing line is left
    unconsumed so a record still being written is read whole on the next poll.
    """
    p = audit.path()
    try:
        size = os.path.getsize(p)
    except OSError:
        return [], 0
    trim = False
    if offset is None or offset > size or offset < 0:
        offset = max(0, size - FEED_TAIL_BYTES)
        trim = offset > 0        # the first line is a fragment; skip it
    try:
        with open(p, "rb") as f:
            f.seek(offset)
            chunk = f.read()
    except OSError:
        return [], offset
    head, sep, _partial = chunk.rpartition(b"\n")
    if not sep:
        return [], offset        # no complete line yet
    lines = head.split(b"\n")
    if trim:
        lines = lines[1:]
    out = []
    for raw in lines:
        if raw.strip():
            with contextlib.suppress(ValueError):
                out.append(json.loads(raw))
    return out, offset + len(head) + 1


def public_record(rec: dict) -> dict:
    out = {k: rec[k] for k in FEED_FIELDS if k in rec}
    detail = rec.get("detail")
    if isinstance(detail, dict):
        out["detail"] = {k: detail[k] for k in DETAIL_FIELDS if k in detail}
    return out


def mine(records: list[dict], caller: str) -> list[dict]:
    """Only this visitor's own decisions. A record with no caller_id - a
    reservation expiring, a webhook effect - is dropped rather than guessed at,
    so nothing another caller produced can appear in this response."""
    return [r for r in records if r.get("caller_id") == caller]


# ------------------------------------------------------------------ block

def block_json(conn, caller: str) -> dict:
    b = ledger.snapshot(conn, caller)
    if b is None:
        return {}
    # No block_id: it is the handle /revoke takes, and a public page has no use
    # for one. The visitor revokes through their own session instead.
    return {"currency": b.currency, "reserved": b.reserved, "spent": b.spent,
            "held": b.held, "available": b.available,
            "expires_at": ledger.iso(b.expires_at),
            "revoked": b.revoked_at is not None,
            "frozen": b.frozen_at is not None, "freeze_reason": b.freeze_reason}


def decision_json(d, ref=None) -> dict:
    out = {"outcome": d.outcome, "rule": d.rule, "reason": d.reason,
           "detail": {k: v for k, v in d.detail.items() if k in DETAIL_FIELDS}}
    if ref is not None and d.outcome == HOLD:
        out["call_id"] = ref.reservation_id
    return out


def _bind_demo_order(conn, ref: ledger.Ref, amount, currency, receipt) -> None:
    """Close out the hold the way a real order would.

    Without this the idempotency row keeps `result IS NULL` and the same call
    reads as still in flight (R7) forever, so a judge clicking twice would see a
    refusal that means nothing. The id is marked `demo_` because no order of
    that name exists at Razorpay and the page must never imply otherwise.
    """
    with contextlib.suppress(Exception):
        ledger.settle_order(conn, ref, order_id="demo_" + ref.reservation_id,
                            result={"id": "demo_" + ref.reservation_id, "amount": amount,
                                    "currency": currency, "receipt": receipt,
                                    "note": "created by the demo site; no upstream call"})


def place(conn, caller: str, cfg: Config, token: str, *, amount, currency,
          receipt: str | None, key: str | None, tool: str = "create_order",
          idem_args: dict | None = None) -> dict:
    """One money call against this visitor's block, decided for real.

    No upstream call is made, which is exactly how `--overspend` already works:
    the decision, the hold and the audit record are real, and nothing is sent to
    Razorpay. So the page cannot burn a test-mode rate limit, cannot look broken
    when Razorpay is slow, and cannot move a rupee of anyone's money.
    """
    call = Call(tool=tool, caller_id=caller, amount=amount, currency=currency,
                idem_key=key)
    d, ref = ledger.authorize(conn, call, cfg, receipt=receipt,
                              idempotency_args=idem_args)
    if ref is not None and d.outcome == HOLD:
        _HOLDS[ref.reservation_id] = (token, ref)
    if ref is not None and d.outcome == ALLOW and not d.detail.get("replay"):
        _bind_demo_order(conn, ref, amount, currency, receipt)
    return decision_json(d, ref)


# ================================================================== routes

async def api_session(request):
    token = session_token(request)
    conn = ledger.connect()
    try:
        ledger.init(conn, config_of(token), caller_id=caller_of(token))
        payload = {"block": block_json(conn, caller_of(token)),
                   "limits": dataclasses.asdict(config_of(token)),
                   "custom": token in _SESSIONS}
    finally:
        conn.close()
    return reply(payload, token, request)


async def api_session_reset(request):
    """Rebuild this visitor's block, with limits they choose.

    Reserve Pay exists so a user can set spending limits for a merchant, so this
    is the pitch made literal. A limit change is a reset and never a mid-life
    mutation: changing a live block's cap would need a config-change state
    machine the ledger deliberately does not have. Resetting mints a fresh
    session token, so the new block is a new caller and the old one is simply
    left behind - which is what a Render spin-down does to it anyway.
    """
    body = await body_of(request)
    chosen, errors = {}, []
    for field in ("reserved", "max_txn", "approval_over"):
        if field not in body:
            continue
        value = body[field]
        if type(value) is not int or not 100 <= value <= 10 ** 9:
            errors.append(f"{field} must be a whole number of paise between 100 and 10^9")
        else:
            chosen[field] = value
    cfg = dataclasses.replace(default_config(), **chosen)
    if not errors:
        if cfg.approval_over >= cfg.max_txn:
            errors.append("the approval line has to sit below the per-call cap, or R5"
                          " refuses every call that would need approval and no call can"
                          " ever reach a hold")
        if cfg.max_txn > cfg.reserved:
            errors.append("the per-call cap cannot be larger than the whole block")
    if errors:
        return reply({"error": errors[0]}, session_token(request), request, 400)

    token = secrets.token_urlsafe(16)
    if chosen:
        _SESSIONS[token] = cfg
    conn = ledger.connect()
    try:
        ledger.init(conn, cfg, caller_id=caller_of(token))
        payload = {"block": block_json(conn, caller_of(token)),
                   "limits": dataclasses.asdict(cfg), "custom": bool(chosen)}
    finally:
        conn.close()
    return reply(payload, token, request)


async def api_catalogue(request):
    return reply(catalogue(), session_token(request), request)


async def api_feed(request):
    token = session_token(request)
    raw = request.query_params.get("after")
    offset = int(raw) if raw and raw.lstrip("-").isdigit() else None
    records, cursor = read_tail(offset)
    return reply({"cursor": cursor,
                  "records": [public_record(r) for r in mine(records, caller_of(token))]},
                 token, request)


async def api_raw(request):
    """This visitor's own records, as the raw JSONL they were written as.

    "Show the audit trail" is the literal judging bar and this is its most
    literal answer. Still whitelisted field by field: raw means the format, not
    the ids of a machine the visitor does not own.
    """
    token = session_token(request)
    records, _ = read_tail(None)
    lines = [json.dumps(public_record(r), sort_keys=True)
             for r in mine(records, caller_of(token))]
    r = PlainTextResponse("\n".join(lines) + ("\n" if lines else ""))
    r.headers["Content-Disposition"] = 'attachment; filename="my-audit-records.jsonl"'
    return r


async def api_shop(request):
    """Send the agent shopping, or buy a chosen list of catalogue items."""
    token = session_token(request)
    body = await body_of(request)
    wanted = body.get("items")
    items = {i["id"]: i for i in catalogue().get("items", [])}
    if isinstance(wanted, list) and wanted:
        chosen = [items[i] for i in wanted[:20] if isinstance(i, str) and i in items]
    else:
        chosen = sorted((i for i in items.values() if i.get("basket")),
                        key=lambda i: i["basket"])
    if not chosen:
        return reply({"error": "nothing in that basket is on the shelf"}, token, request, 400)

    run, cfg, caller = secrets.token_urlsafe(4), config_of(token), caller_of(token)
    currency = catalogue().get("currency", "INR")
    conn = ledger.connect()
    try:
        ledger.init(conn, cfg, caller_id=caller)
        results = []
        for n, item in enumerate(chosen):
            d = place(conn, caller, cfg, token, amount=item["paise"], currency=currency,
                      receipt=item["name"][:40], key=f"demo-{run}-{n}",
                      idem_args={"amount": item["paise"], "receipt": item["name"][:40]})
            results.append({"id": item["id"], "name": item["name"],
                            "paise": item["paise"], **d})
        payload = {"results": results, "block": block_json(conn, caller)}
    finally:
        conn.close()
    return reply(payload, token, request)


async def api_attack(request):
    """One hand-built call, decided by the same rules as the agent's."""
    token = session_token(request)
    body = await body_of(request)
    tool = body.get("tool") if isinstance(body.get("tool"), str) else "create_order"
    receipt = body.get("receipt") if isinstance(body.get("receipt"), str) else None
    key = body.get("idempotency_key")
    cfg, caller = config_of(token), caller_of(token)
    conn = ledger.connect()
    try:
        ledger.init(conn, cfg, caller_id=caller)
        # amount and currency go through untouched: a string, a float, a boolean
        # or a missing field has to reach R0 as itself. Coercing here would be
        # the gate answering its own exam.
        d = place(conn, caller, cfg, token, amount=body.get("amount"),
                  currency=body.get("currency", "INR"),
                  receipt=receipt[:40] if receipt else None,
                  key=key if isinstance(key, str) and key else None, tool=tool[:60],
                  idem_args={"amount": body.get("amount"), "receipt": receipt})
        payload = {"decision": d, "block": block_json(conn, caller)}
    finally:
        conn.close()
    return reply(payload, token, request)


async def api_twin(request):
    """B15, watched rather than asserted.

    The same call is decided twice - once with the visitor's free text, once
    without. The decisions are identical because the two `policy.Call` objects
    are identical: `Call` has no field a product name could occupy. decide() is
    pure (G5), so neither run writes anything at all.
    """
    token = session_token(request)
    body = await body_of(request)
    text = body.get("text") if isinstance(body.get("text"), str) else ""
    cfg, caller = config_of(token), caller_of(token)
    conn = ledger.connect()
    try:
        ledger.init(conn, cfg, caller_id=caller)
        block = ledger.snapshot(conn, caller)
    finally:
        conn.close()

    def run(free_text: str) -> dict:
        # `free_text` would ride on the wire as the receipt and the notes. There
        # is nowhere on Call to put it, which is the entire demonstration.
        call = Call(tool="create_order", caller_id=caller,
                    amount=body.get("amount", 150000),
                    currency=body.get("currency", "INR"))
        d = decide(call, State(block=block), cfg, ledger.now_utc())
        return {"free_text": free_text,
                "call": json.dumps(dataclasses.asdict(call), sort_keys=True, default=str),
                "decision": decision_json(d)}

    with_text, without = run(text[:2000]), run("")
    return reply({"with_text": with_text, "without_text": without,
                  "identical": with_text["call"] == without["call"]
                  and with_text["decision"] == without["decision"],
                  "call_fields": [f.name for f in dataclasses.fields(Call)]},
                 token, request)


async def api_approve(request):
    """Release this visitor's own HOLD.

    Scoped by construction: only place() ever puts a Ref in _HOLDS, and the
    session that took it is checked here, so a call id belonging to the agent's
    own holds is simply not found. Nothing reaches src.server, so G12's
    separation between the agent's token and the operator's is untouched.
    """
    token = session_token(request)
    body = await body_of(request)
    call_id = body.get("call_id")
    pending = _HOLDS.get(call_id) if isinstance(call_id, str) else None
    if pending is None or pending[0] != token:
        return reply({"error": "no hold of yours is waiting under that id"},
                     token, request, 404)
    _HOLDS.pop(call_id, None)
    ref = pending[1]
    cfg, caller = config_of(token), caller_of(token)
    conn = ledger.connect()
    try:
        if not ledger.renew_hold(conn, ref, cfg.reservation_ttl_minutes):
            return reply({"error": "this hold is no longer approvable: it expired, or its"
                          " block was revoked, expired or frozen"}, token, request, 410)
        row = conn.execute("SELECT amount, currency FROM reservations"
                           " WHERE reservation_id = ?", (ref.reservation_id,)).fetchone()
        _bind_demo_order(conn, ref, row["amount"], row["currency"], None)
        audit.record(event="hold_approved", kind="money", caller_id=caller,
                     amount=row["amount"], currency=row["currency"],
                     reason="approved by the visitor, who is the operator of their own"
                            " demo block", reservation_id=ref.reservation_id)
        payload = {"approved": call_id, "block": block_json(conn, caller)}
    finally:
        conn.close()
    return reply(payload, token, request)


async def api_revoke(request):
    """R4 on this visitor's own block. Consent withdrawn: the next call refuses."""
    token = session_token(request)
    cfg, caller = config_of(token), caller_of(token)
    conn = ledger.connect()
    try:
        block_id = ledger.init(conn, cfg, caller_id=caller)
        changed = ledger.revoke(conn, block_id)
        payload = {"revoked": changed, "block": block_json(conn, caller)}
    finally:
        conn.close()
    return reply(payload, token, request)


async def api_expire(request):
    """R2, without waiting thirty days. The expiry instant itself refuses, so
    the block is dated at the current second rather than a moment after it."""
    token = session_token(request)
    cfg, caller = config_of(token), caller_of(token)
    conn = ledger.connect()
    try:
        ledger.init(conn, cfg, caller_id=caller)
        conn.execute("UPDATE blocks SET expires_at = ? WHERE caller_id = ?",
                     (ledger.iso(ledger.now_utc()), caller))
        audit.record(event="block_expired_by_visitor", kind="money", caller_id=caller,
                     reason="the demo page moved this block's expiry to now")
        payload = {"expired": True, "block": block_json(conn, caller)}
    finally:
        conn.close()
    return reply(payload, token, request)


# ------------------------------------------------------------ webhook panel

WEBHOOK_VARIANTS = ("apply", "again", "out_of_order", "bad_signature", "changed_amount")


async def api_webhook_replay(request):
    """Deliver a signed event to this visitor's block - again, out of order, or
    with a bad signature.

    The real `webhook.validate` and the real `ledger.reconcile_webhook` run. The
    event is signed with a secret this process generated, not with
    RAZORPAY_WEBHOOK_SECRET: the HMAC path is what is being shown, and Razorpay's
    signing key has no business being reachable from a public page.
    """
    token = session_token(request)
    body = await body_of(request)
    variant = body.get("variant")
    if variant not in WEBHOOK_VARIANTS:
        return reply({"error": "unknown variant"}, token, request, 400)

    caller = caller_of(token)
    conn = ledger.connect()
    try:
        ledger.init(conn, config_of(token), caller_id=caller)
        r = conn.execute(
            "SELECT r.order_id, r.amount, r.currency FROM reservations r"
            " JOIN blocks b ON b.block_id = r.block_id"
            " WHERE b.caller_id = ? AND r.state = 'held' AND r.order_id IS NOT NULL"
            " ORDER BY r.created_at DESC, r.rowid DESC LIMIT 1", (caller,)).fetchone()
        if r is None:
            return reply({"error": "buy something first - a webhook settles an order the"
                          " block is already holding"}, token, request, 409)

        if variant == "again":
            event_id = _LAST_EVENT.get(token)
            if not event_id:
                return reply({"error": "send one first, then send the same one again"},
                             token, request, 409)
        else:
            event_id = "evt_demo_" + secrets.token_urlsafe(8)

        payload = {"event": "order.paid" if variant == "out_of_order" else "payment.captured",
                   "payload": {"payment": {"entity": {
                       "id": "pay_demo_" + token[:12], "order_id": r["order_id"],
                       "amount": r["amount"] + 1 if variant == "changed_amount" else r["amount"],
                       "currency": r["currency"], "status": "captured"}}}}
        raw = json.dumps(payload, separators=(",", ":")).encode()
        signature = hmac.new(_DEMO_SECRET.encode(), raw, hashlib.sha256).hexdigest()
        if variant == "bad_signature":
            signature = "0" * len(signature)

        status, reason, parsed = webhook.validate(raw, signature, event_id, _DEMO_SECRET)
        if status != 200:
            audit.record(event="webhook_reject", kind="demo", caller_id=caller,
                         event_id=event_id, reason=reason)
            out = {"http_status": status, "effect": "REJECT", "applied": False,
                   "reason": reason,
                   "note": "rejected before the body was parsed (G10)"}
        else:
            if variant != "again":
                _LAST_EVENT[token] = event_id
            out = {"http_status": 200,
                   **ledger.reconcile_webhook(conn, event_id, parsed["event"],
                                              parsed["payload"]["payment"]["entity"])}
        out["variant"] = variant
        out["block"] = block_json(conn, caller)
    finally:
        conn.close()
    return reply(out, token, request)


# ---------------------------------------------------------------- mutations

_MUTATE_LOCK = asyncio.Lock()


@contextlib.contextmanager
def _audit_aside():
    """A mutation run writes hundreds of records through a deliberately broken
    gate. They must not land in the file whose chain the report verifies, and
    the process's own chain position must not be left pointing at them."""
    saved = os.environ.get("RESERVE_GATE_AUDIT")
    os.environ["RESERVE_GATE_AUDIT"] = os.path.join(tempfile.gettempdir(),
                                                    "reserve-gate-demo-mutation.jsonl")
    audit._prev_hash = audit._prev_path = None
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("RESERVE_GATE_AUDIT", None)
        else:
            os.environ["RESERVE_GATE_AUDIT"] = saved
        audit._prev_hash = audit._prev_path = None


@functools.lru_cache(maxsize=1)
def _mutation_inputs():
    """harness/mutate.py plus its cases, imported once.

    The import happens inside _audit_aside because that module redirects
    RESERVE_GATE_AUDIT at import time; without the guard it would point the
    whole server's audit log at a temporary directory.
    """
    if str(ROOT / "harness") not in sys.path:
        sys.path.insert(0, str(ROOT / "harness"))
    with _audit_aside():
        import mutate
    cases = [json.loads(line) for line in artefact("harness/cases.jsonl").splitlines()
             if line.strip()]
    return mutate, cases, artefact("src/policy.py")


async def api_mutations(request):
    mutate, cases, _ = _mutation_inputs()
    return reply({"cases": len(cases),
                  "mutations": [{"index": n, "label": label, "anchor": anchor}
                                for n, (label, anchor) in enumerate(mutate.MUTATIONS)],
                  "report": artefact("harness/mutation_report.md")},
                 session_token(request), request)


async def api_mutate(request):
    """Re-score the 150 cases with one guard deleted, in a process of its own.

    The mutation is chosen by index into the fixed MUTATIONS list. Caller-supplied
    source is never accepted, never concatenated and never exec'd: `score_with`
    compiles Python, so a string from a request reaching it would be remote code
    execution, and that is the one line in this module worth being careful about.

    It runs as a subprocess because `score_with` rebinds `ledger.decide` for the
    whole interpreter. Scored in here, an unauthenticated request would delete a
    guard from every concurrent money decision for the ~0.8 s it takes. The
    single-threaded event loop happens to make that hard to hit today, which is
    not a property worth resting a money control on: one `await` added inside the
    scoring path would open it, and nothing would fail. Measured 31 Aug 2026: the
    child costs 268 ms to start and imports no MCP, against a 2000 ms budget.

    The lock stays, now only to bound CPU - one scoring job at a time rather than
    one per visitor.
    """
    token = session_token(request)
    body = await body_of(request)
    index = body.get("index")
    mutate, _, _ = _mutation_inputs()
    baseline = index is None
    if not baseline and (type(index) is not int or not 0 <= index < len(mutate.MUTATIONS)):
        return reply({"error": "no such mutation"}, token, request, 400)

    args = ["--baseline"] if baseline else ["--index", str(index)]
    async with _MUTATE_LOCK:
        started = time.perf_counter()
        child = await asyncio.create_subprocess_exec(
            sys.executable, str(ROOT / "harness" / "mutate.py"), *args,
            cwd=str(ROOT), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(child.communicate(), timeout=60)
        except asyncio.TimeoutError:
            child.kill()
            await child.wait()
            return reply({"error": "the mutation run timed out"}, token, request, 504)
        seconds = round(time.perf_counter() - started, 3)

    try:
        score = json.loads(out.decode("utf-8", "replace") or "{}")
    except json.JSONDecodeError:
        score = {}
    if child.returncode != 0 or "false_allow" not in score:
        # The child's stderr is a traceback over our own source and never reaches
        # the page; the log gets it, the visitor gets the reason.
        print("mutation child failed:", child.returncode, err.decode("utf-8", "replace")[:800])
        return reply({"error": score.get("error", "the mutation run failed")},
                     token, request, 500)
    label = score.pop("label")
    cases_scored = score.pop("cases")

    dirty = score["false_allow"] + score["wrong_rule"] + score["wrong_effect"] + score["twins"]
    return reply({"index": None if baseline else index, "label": label, "baseline": baseline,
                  "cases": cases_scored, "seconds": seconds,
                  # For the baseline the good answer is clean; for a mutation it
                  # is the opposite - a row that stays clean means no case
                  # depends on that rule and the harness is not measuring it.
                  "ok": dirty == 0 if baseline else dirty > 0, **score},
                 token, request)


# ------------------------------------------------------------- the artefacts

async def api_rules(request):
    return reply({"config": dataclasses.asdict(default_config()),
                  "rules": json.loads(artefact("web/rules.json") or "{}"),
                  "provenance": artefact("harness/provenance.md")},
                 session_token(request), request)


@functools.lru_cache(maxsize=1)
def limitations() -> str:
    """The README's own limitations section, so there is one source of truth for
    the gaps. §6 Day 6 reserves a beat for naming a failure that is *not*
    handled; a judge who watches you name your own holes trusts the rest."""
    text = artefact("README.md")
    start = text.find("## Honest limitations")
    if start < 0:
        return ""
    end = text.find("\n## ", start + 1)
    return text[start:end if end > 0 else len(text)].strip()


async def api_evidence(request):
    chain = sample_chain()
    return reply({"eval_report": artefact("eval_report.md"),
                  "mutation_report": artefact("harness/mutation_report.md"),
                  "architecture": artefact("ARCHITECTURE.md"),
                  "limitations": limitations(),
                  "chain": {"verified": chain["verified"], "bad_line": chain["bad_line"],
                            "tail": chain["tail"], "records": len(chain["records"])},
                  "models": model_rows(),
                  "ots": ots_proof()},
                 session_token(request), request)


async def api_tamper(request):
    """B22 on an in-memory copy. One flipped byte, and verify() names the line it
    broke. The committed file is never opened for writing."""
    chain = sample_chain()
    lines = artefact(SAMPLE).splitlines()
    if len(lines) < 2:
        return reply({"error": "no sample to tamper with"}, session_token(request),
                     request, 500)
    edited = list(lines)
    edited[1] = (edited[1].replace('"create_order"', '"capture_payment"', 1)
                 if '"create_order"' in edited[1] else edited[1].replace('"allow"', '"block"', 1))
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "tampered.jsonl")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(edited) + "\n")
        ok, bad = audit.verify(p)
    return reply({"before": {"verified": chain["verified"], "bad_line": chain["bad_line"]},
                  "after": {"verified": ok, "bad_line": bad}, "edited_line": 2,
                  "note": f"the edit happened in memory; {SAMPLE} on disk is"
                          " byte-identical"},
                 session_token(request), request)


TRACE_FIELDS = ("ts", "event", "tool", "amount", "currency", "rule", "reason",
                "receipt", "order_id", "hash", "prev_hash")


async def api_trace(request):
    """One purchase, end to end, from the committed audit_sample.jsonl.

    That file holds a genuine settlement - an order created, a payment made in a
    browser, a capture back through the gate - so this answers "show me money
    actually moving" rather than showing another refusal.
    """
    chain = sample_chain()
    order = request.query_params.get("order")
    if not order:
        return reply({"purchases": chain["purchases"]}, session_token(request), request)
    wanted = {res for res, oid in chain["by_order"].items() if oid == order}
    if not wanted:
        return reply({"error": "no such purchase in the sample"},
                     session_token(request), request, 404)
    steps = [{**{k: r[k] for k in TRACE_FIELDS if k in r}, "reservation_id": _res_of(r)}
             for r in chain["records"] if _res_of(r) in wanted]
    return reply({"order_id": order, "steps": steps,
                  "purchase": next((p for p in chain["purchases"]
                                    if p["order_id"] == order), None)},
                 session_token(request), request)


# ------------------------------------------------------------------ the box

# Not the buyer's gemini-3.6-flash: that model gives 20 requests per day on this
# account and one question can loop several tool calls. Flash Lite gives 500 a
# day and 15 a minute. Both figures are read off the AI Studio rate-limit page
# on 30 Aug 2026, not observed by exhausting a quota - so treat the ceilings
# below as vendor-stated, and the budget as the thing that keeps us clear of
# finding out.
LLM_MODEL = os.environ.get("RESERVE_GATE_LLM_MODEL", "gemini-3.1-flash-lite")
LLM_CALLS_PER_QUESTION = 4      # propose -> refusal -> answer, with one spare
LLM_QUESTIONS_PER_VISITOR = 3
LLM_DAILY_CALLS = 300           # of 500, leaving headroom for video takes
LLM_RPM = 12                    # under the measured 15-a-minute ceiling

_LLM_LOCK = asyncio.Lock()      # one question in flight at a time
_LLM_RECENT: list[float] = []   # a token bucket: a burst queues, never refuses
_LLM_ASKED: dict[str, int] = {}


def llm_budget(conn, spend: int = 0) -> int:
    """Model calls left today. The counter lives in SQLite so it survives a
    restart within the day, and is keyed by the UTC date so it resets at
    midnight without anything having to run at midnight."""
    conn.execute("CREATE TABLE IF NOT EXISTS demo_llm_budget ("
                 " day TEXT PRIMARY KEY, calls INTEGER NOT NULL DEFAULT 0)")
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if spend:
        conn.execute("INSERT INTO demo_llm_budget (day, calls) VALUES (?, ?)"
                     " ON CONFLICT(day) DO UPDATE SET calls = calls + ?",
                     (day, spend, spend))
    row = conn.execute("SELECT calls FROM demo_llm_budget WHERE day = ?", (day,)).fetchone()
    return LLM_DAILY_CALLS - (row["calls"] if row else 0)


@functools.lru_cache(maxsize=1)
def recorded_run() -> dict:
    """A genuine captured session, committed to the repo. Not invented text: the
    box never shows an error and it never shows a lie."""
    return json.loads(artefact("web/recorded_llm_run.json") or "{}")


async def _throttle() -> None:
    now = time.monotonic()
    _LLM_RECENT[:] = [t for t in _LLM_RECENT if now - t < 60]
    if len(_LLM_RECENT) >= LLM_RPM:
        await asyncio.sleep(max(0.0, 60 - (now - _LLM_RECENT[0])))
        _LLM_RECENT[:] = [t for t in _LLM_RECENT if time.monotonic() - t < 60]
    _LLM_RECENT.append(time.monotonic())


async def _ask_model(question: str, token: str) -> tuple[list[dict], str, int]:
    """Imported here and never at module scope: the three-command repro has to
    run in a fresh clone with no model SDK installed (E10), and this is also the
    largest import on a tier where the cold start is the whole latency budget."""
    from google import genai
    from google.genai import types

    cfg, caller = config_of(token), caller_of(token)
    turns: list[dict] = []

    def create_order(amount: int, currency: str = "INR", item: str = "") -> dict:
        """Buy one item. `amount` is in paise, the smallest unit of the currency,
        so 50000 means 500.00 rupees. `item` is what is being bought."""
        conn = ledger.connect()
        try:
            ledger.init(conn, cfg, caller_id=caller)
            d = place(conn, caller, cfg, token, amount=amount, currency=currency,
                      receipt=item[:40] or None, key="ask-" + secrets.token_urlsafe(6),
                      idem_args={"amount": amount, "item": item})
        finally:
            conn.close()
        turns.append({"tool": "create_order", "amount": amount, "currency": currency,
                      "item": item[:60], "gate": d})
        return d

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    response = await client.aio.models.generate_content(
        model=LLM_MODEL,
        contents="You are shopping through the reserve_gate tools. " + question,
        config=types.GenerateContentConfig(
            # temperature 0: the same question on camera has to give the same
            # answer in take two as in take one.
            temperature=0, tools=[create_order],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                maximum_remote_calls=LLM_CALLS_PER_QUESTION)))
    return turns, response.text or "", len(turns) + 1


async def api_ask(request):
    """A real model proposes a call; this gate answers it.

    When the daily budget is spent, the key is absent, or the model errors, the
    box replays the recorded run and labels it as one.
    """
    token = session_token(request)
    body = await body_of(request)
    question = body.get("question")
    if not isinstance(question, str) or not question.strip():
        return reply({"error": "ask something"}, token, request, 400)

    conn = ledger.connect()
    try:
        left = llm_budget(conn)
    finally:
        conn.close()
    asked = _LLM_ASKED.get(token, 0)
    why = None
    if asked >= LLM_QUESTIONS_PER_VISITOR:
        why = "you have used your three questions"
    elif left < LLM_CALLS_PER_QUESTION:
        why = "today's model budget is spent"
    elif not os.environ.get("GEMINI_API_KEY"):
        why = "no model key is configured on this host"
    if why:
        return reply({"live": False, "reason": why, **recorded_run()}, token, request)

    async with _LLM_LOCK:
        await _throttle()
        try:
            turns, answer, used = await _ask_model(question[:500], token)
        except Exception as e:      # noqa: BLE001 - any model fault falls back
            audit.record(event="llm_error", kind="demo", error=f"{type(e).__name__}: {e}")
            return reply({"live": False, "reason": "the model call failed, so this is the"
                          " recorded run", **recorded_run()}, token, request)

    _LLM_ASKED[token] = asked + 1
    conn = ledger.connect()
    try:
        llm_budget(conn, spend=used)
    finally:
        conn.close()
    return reply({"live": True, "model": LLM_MODEL, "question": question[:500],
                  "turns": turns, "answer": answer,
                  "questions_left": LLM_QUESTIONS_PER_VISITOR - asked - 1}, token, request)


# ------------------------------------------------------------------- static

async def page(request):
    name = PAGES[request.scope["path"]]
    body, etag = static(name)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return Response(body, media_type=MEDIA.get(pathlib.Path(name).suffix,
                                               "application/octet-stream"),
                    # Short, and paired with the ETag. The site is six separate
                    # pages, so moving between them is a full reload; without
                    # this every click re-downloads the CSS and the JS instead
                    # of getting a 304.
                    headers={"ETag": etag, "Cache-Control": "public, max-age=60"})


# --------------------------------------------------------------- error pages

# What each status says to a visitor. The gate line is the point of the second
# column: this host moves money, and someone who lands on a broken page needs to
# know within one sentence whether their balance is affected.
ERRORS = {
    404: ("Not found",
          "That address is not part of this site.",
          "Nothing was charged, and no limit changed."),
    405: ("Wrong method",
          "That address exists, but not for the way it was asked for.",
          "Nothing was charged, and no limit changed."),
    500: ("Something broke here",
          "This page failed to render. The failure is ours, not yours.",
          "The gate refuses by default, so no spend passed while this was broken."),
    503: ("Temporarily unavailable",
          "The site is up but a part it depends on is not answering.",
          "The gate refuses by default, so no spend passed while this was broken."),
}

# Inlined rather than served from app.css, and that is the whole point: this is
# the page shown when something is already broken, so it must not depend on a
# second request succeeding. No webfont, no stylesheet, no script. The two
# colours are copies of --color-paper and --color-blue; if the palette moves,
# move them here too - a duplicated token beats a page that cannot paint.
_ERROR_TEMPLATE = """<!doctype html>
<html lang="en" data-skin="night">
<head>
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="alternate icon" href="/favicon.png" type="image/png">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{code} — reserve-gate</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh; display: grid; place-items: center;
    padding: 2rem; background: #111112; color: #f4f4ed;
    font: 400 1rem/1.55 ui-sans-serif, system-ui, "Segoe UI", sans-serif;
  }}
  main {{ max-width: 34rem; }}
  .code {{
    font: 700 0.8rem/1 ui-monospace, "Cascadia Mono", monospace;
    letter-spacing: .18em; color: #d2ff00; margin: 0 0 1.25rem;
  }}
  h1 {{ font-size: clamp(1.9rem, 5vw, 2.75rem); line-height: 1.1; margin: 0 0 1rem; font-weight: 500; }}
  p {{ margin: 0 0 .75rem; color: #a9a9a2; }}
  .gate {{ color: #f4f4ed; }}
  a {{
    display: inline-block; margin-top: 1.75rem; padding: .7rem 1.4rem;
    background: #d2ff00; color: #111112; text-decoration: none;
    font: 700 .8rem/1 ui-monospace, "Cascadia Mono", monospace; letter-spacing: .12em;
  }}
  a:focus-visible {{ outline: 2px solid #f4f4ed; outline-offset: 3px; }}
</style>
</head>
<body>
<main>
  <p class="code">ERROR {code}</p>
  <h1>{title}</h1>
  <p>{detail}</p>
  <p class="gate">{gate}</p>
  <a href="/">Back to the demo</a>
</main>
</body>
</html>
"""


@functools.lru_cache(maxsize=len(ERRORS))
def error_html(status: int) -> bytes:
    """The page body for one status. Cached: these never change at runtime."""
    title, detail, gate = ERRORS.get(status, ERRORS[500])
    return _ERROR_TEMPLATE.format(code=status, title=title,
                                  detail=detail, gate=gate).encode()


def wants_html(scope) -> bool:
    """Is this a browser navigating, rather than an agent calling the API?

    Read off Accept alone. A person gets a page and a client gets JSON, and the
    status code is the same either way - the representation changes, never the
    answer.
    """
    accept = dict(scope.get("headers") or {}).get(b"accept", b"")
    return b"text/html" in accept


def error_response(status: int, scope):
    """One status, rendered for whoever asked."""
    if wants_html(scope):
        return Response(error_html(status), status_code=status,
                        media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "no-store"})
    title = ERRORS.get(status, ERRORS[500])[0]
    return JSONResponse({"error": title.lower().replace(" ", "_")}, status)


ROUTES = [
    ("/api/session", ["GET"], api_session),
    ("/api/session/reset", ["POST"], api_session_reset),
    ("/api/catalogue", ["GET"], api_catalogue),
    ("/api/feed", ["GET"], api_feed),
    ("/api/raw", ["GET"], api_raw),
    ("/api/shop", ["POST"], api_shop),
    ("/api/attack", ["POST"], api_attack),
    ("/api/twin", ["POST"], api_twin),
    ("/api/approve", ["POST"], api_approve),
    ("/api/revoke", ["POST"], api_revoke),
    ("/api/expire", ["POST"], api_expire),
    ("/api/webhook-replay", ["POST"], api_webhook_replay),
    ("/api/mutations", ["GET"], api_mutations),
    ("/api/mutate", ["POST"], api_mutate),
    ("/api/rules", ["GET"], api_rules),
    ("/api/evidence", ["GET"], api_evidence),
    ("/api/tamper", ["POST"], api_tamper),
    ("/api/trace", ["GET"], api_trace),
    ("/api/ask", ["POST"], api_ask),
    ("/api/live-checkout/order", ["POST"], api_live_order),
    ("/api/live-checkout/capture", ["POST"], api_live_capture),
] + [(path, ["GET"], page) for path in PAGES]

# Every path this module serves, and the only thing bearer_auth opens. It is a
# closed set rather than a prefix: a route added here is opened deliberately.
PUBLIC_PATHS = frozenset(path for path, _, _ in ROUTES)


def install(mcp, *, live_available=None, live_order=None, live_capture=None) -> None:
    global _LIVE_AVAILABLE, _LIVE_ORDER, _LIVE_CAPTURE
    _LIVE_AVAILABLE, _LIVE_ORDER, _LIVE_CAPTURE = live_available, live_order, live_capture
    for path, methods, handler in ROUTES:
        mcp.custom_route(path, methods)(handler)
