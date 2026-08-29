"""reserve-gate: an MCP server that sits between an AI buyer and Razorpay.

Four tools are declared here by hand. That declaration *is* the allowlist:
a Razorpay tool that is not written below cannot be reached through this
server, including any tool Razorpay adds after this was written.

Money tools route through ledger.authorize(), which takes the decision and the
hold under one write lock. Reads pass through and are logged at a lower level.
Nothing in this file decides anything: the rules live in policy.py and the state
lives in ledger.py, so a refusal can be explained without reading the transport.
"""
import argparse
import functools
import os
import secrets
from dataclasses import replace
from typing import Any

from starlette.responses import JSONResponse

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from . import audit, ledger
from .policy import HOLD, Call, load_config
from .upstream import UpstreamError, call_razorpay
from .webhook import handle as handle_webhook

# Paths the operator uses and the agent must never reach. Checked against
# RESERVE_GATE_ADMIN_TOKEN, a different secret from the one the agent holds.
ADMIN_PATHS = ("/approve/", "/revoke/", "/unfreeze/", "/block")

# call_id -> (tool, upstream args, Ref). A HOLD parks here until POST /approve.
# ponytail: in memory, so a restart forgets the pending approvals. The hold
# itself is a row in SQLite and its TTL returns it to the block either way
# (B33), so a restart costs an approval, never a lost or leaked balance.
_HOLDS: dict[str, tuple[str, dict, ledger.Ref]] = {}


def _allowed_hosts() -> list[str]:
    """Host values this server will answer to.

    The MCP transport rejects an unrecognised Host header to block DNS
    rebinding, so the public hostname has to be declared. Render publishes it
    as RENDER_EXTERNAL_HOSTNAME; locally only loopback is allowed.
    """
    hosts = ["localhost", "127.0.0.1", "localhost:*", "127.0.0.1:*"]
    external = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    if external:
        hosts += [external, f"{external}:*"]
    return hosts


# Underscored, not hyphenated: some MCP clients reject a hyphen in a server name.
mcp = FastMCP("reserve_gate", transport_security=TransportSecuritySettings(
    allowed_hosts=_allowed_hosts(),
    allowed_origins=[f"https://{h}" for h in _allowed_hosts()] +
                    [f"http://{h}" for h in _allowed_hosts()],
))


@functools.lru_cache(maxsize=1)
def config():
    """Read once. A malformed policy.yaml then fails at startup rather than at
    the moment a money call needs a number out of it."""
    return load_config(os.environ.get("RESERVE_GATE_POLICY", "policy.yaml"))


def caller_id() -> str:
    """One bearer token, one caller, one block.

    Derived from the verified token and never from anything a caller sends
    (§11.0), so a block cannot be claimed by presenting its id. stdio carries no
    token and gets a fixed local identity: there the threat model is an agent
    that has been manipulated, not a stranger who reached the socket.
    """
    return ledger.caller_id_for(os.environ.get("RESERVE_GATE_TOKEN") or "stdio-local")


def _refusal(decision) -> ValueError:
    """Turn a refusal into a tool error, never a protocol error.

    The MCP spec reserves JSON-RPC errors for unknown tools and malformed
    arguments; a policy refusal is a business logic error and belongs in the
    tool result with isError set. Raising here is how FastMCP produces that. A
    protocol error would make the client retry the refusal — filling the audit
    log with phantom attempts and tripping R6 — and often never reaches the
    model at all, so the agent loops proposing the call it was just refused.
    """
    detail = " ".join(f"{k}={v}" for k, v in decision.detail.items() if k != "result")
    return ValueError(f"{decision.outcome} [{decision.rule}] {decision.reason}"
                      + (f" | {detail}" if detail else ""))


async def _settle(conn, tool: str, ref: ledger.Ref, args: dict) -> Any:
    """Forward an approved call and close out the hold it is standing on."""
    if tool == "capture_payment":
        # Persist before the network call: a process death after Razorpay acts
        # must not let the ordinary reservation TTL return possibly-spent money.
        ledger.mark_capture_pending(conn, ref)
    try:
        result = await call_razorpay(tool, args)
    except UpstreamError as e:
        if e.known:
            # B25. Razorpay answered and refused, so the call did not happen and
            # the block must not be charged for it.
            ledger.release(conn, ref, reason=str(e))
        else:
            # G14 / B25b. A timeout says nothing about whether Razorpay acted.
            # The hold stays until reconciliation; releasing or expiring it
            # would hand back a balance that was really spent.
            audit.record(event="outcome_unknown", tool=tool, reservation_id=ref.reservation_id,
                         error=str(e), note="hold kept: the upstream outcome is unknown")
        raise ValueError(f"{tool} failed upstream: {e}") from None

    if tool == "create_order":
        ledger.settle_order(conn, ref, order_id=result["id"], result=result)
    elif ledger.settle_capture(conn, ref, result=result) == "refused":
        # The ledger froze the block or declined the debit. Returning Razorpay's
        # payload here would tell the client the money moved as asked.
        raise ValueError("capture refused: the reply does not match the reserved"
                         " payment, and the block is frozen for review")
    return result


async def _gated(call: Call, args: dict) -> Any:
    """The money path. Decide and hold, then forward, then commit or release."""
    conn = ledger.connect()
    try:
        ledger.init(conn, config(), caller_id=call.caller_id)
        # Full args bind client keys; derived keys ignore unstable free text.
        # Call stays free-text-free so the policy decision cannot read it (B15).
        decision, ref = ledger.authorize(
            conn, call, config(), receipt=args.get("receipt"), idempotency_args=args)

        if decision.outcome == HOLD:
            _HOLDS[ref.reservation_id] = (call.tool, args, ref)
            raise _refusal(replace(decision,
                                   detail={**decision.detail, "call_id": ref.reservation_id}))
        if not decision.allowed:
            raise _refusal(decision)
        if decision.detail.get("replay"):
            # R7. The first result, not a second call: the retry must be
            # indistinguishable from the original, without moving money twice.
            return decision.detail["result"]
        return await _settle(conn, call.tool, ref, args)
    finally:
        conn.close()


async def _read(tool: str, args: dict) -> Any:
    """Reads are not gated. They are logged at a lower level, so the audit trail
    shows the scope of the gate honestly rather than implying it covers them."""
    try:
        result = await call_razorpay(tool, args)
    except UpstreamError as e:
        audit.record(event="upstream_error", kind="read", tool=tool, args=args, error=str(e))
        raise ValueError(f"{tool} failed upstream: {e}") from None
    audit.record(event="allow", kind="read", tool=tool, args=args,
                 upstream_id=result.get("id"), status=result.get("status"))
    return result


@mcp.tool()
async def create_order(amount: int, currency: str = "INR", receipt: str | None = None,
                       notes: dict[str, str] | None = None,
                       idempotency_key: str | None = None) -> Any:
    """Create a Razorpay order. `amount` is in paise, the smallest unit of the
    currency, so 50000 means 500.00 rupees. Send a distinct `idempotency_key`
    per purchase; without one the gate derives a key from the call itself and
    remembers it for five minutes, so a retry after a timeout cannot become a
    second real order."""
    args: dict[str, Any] = {"amount": amount, "currency": currency}
    if receipt:
        args["receipt"] = receipt
    if notes:
        args["notes"] = notes
    return await _gated(Call(tool="create_order", caller_id=caller_id(), amount=amount,
                             currency=currency, idem_key=idempotency_key), args)


@mcp.tool()
async def capture_payment(payment_id: str, amount: int, currency: str = "INR",
                          idempotency_key: str | None = None) -> Any:
    """Capture an authorized payment. `amount` is in paise and must equal the
    authorized amount."""
    # The tool is handed a payment id while the reservation is keyed by order id,
    # so Razorpay is asked which order this payment belongs to. Taking it from
    # Razorpay's own reply rather than from an argument means a caller cannot
    # aim a capture at somebody else's order. It is a read, so R6 does not count
    # it, and a failure to resolve it is a refusal rather than a guess.
    try:
        payment = await call_razorpay("fetch_payment", {"payment_id": payment_id})
        order_id = payment["order_id"]
        upstream_amount, upstream_currency = payment["amount"], payment["currency"]
    except (UpstreamError, KeyError, TypeError) as e:
        audit.record(event="block", rule="G4", tool="capture_payment",
                     reason=f"could not resolve the order for {payment_id}: {e}")
        raise ValueError(f"BLOCK [G4] cannot resolve the order for {payment_id}: {e}") from None
    if (type(upstream_amount) is not int or upstream_amount != amount
            or not isinstance(upstream_currency, str)
            or upstream_currency.upper() != currency.upper()):
        audit.record(event="block", rule="R0", tool="capture_payment", payment_id=payment_id,
                     reason="capture arguments do not match Razorpay's payment object")
        raise ValueError("BLOCK [R0] capture amount or currency does not match the payment")
    return await _gated(Call(tool="capture_payment", caller_id=caller_id(), amount=amount,
                             currency=currency, order_id=order_id, payment_id=payment_id,
                             idem_key=idempotency_key),
                        {"payment_id": payment_id, "amount": amount, "currency": currency})


@mcp.tool()
async def fetch_order(order_id: str) -> Any:
    """Read one order by id."""
    return await _read("fetch_order", {"order_id": order_id})


@mcp.tool()
async def fetch_payment(payment_id: str) -> Any:
    """Read one payment by id."""
    return await _read("fetch_payment", {"payment_id": payment_id})


@mcp.custom_route("/health", ["GET"])
async def health(_request):
    """Open on purpose: an uptime check should not need the gate credential.
    The commit and the declared hostname are reported so a deployment can be
    identified without reading the host's dashboard."""
    return JSONResponse({
        "ok": True,
        "service": "reserve-gate",
        "commit": os.environ.get("RENDER_GIT_COMMIT", "local")[:7],
        "allowed_hosts": _allowed_hosts(),
    })


@mcp.custom_route("/webhook", ["POST"])
async def webhook(request):
    return await handle_webhook(request)


@mcp.custom_route("/block", ["GET"])
async def block(_request):
    """The agent's block and what is left of it. Behind the admin token because
    it is the operator's view: it is also where the block id for /revoke comes
    from, and the agent has no business reading either."""
    conn = ledger.connect()
    try:
        ledger.init(conn, config(), caller_id=caller_id())
        b = ledger.snapshot(conn, caller_id())
    finally:
        conn.close()
    return JSONResponse({"block_id": b.block_id, "currency": b.currency,
                         "reserved": b.reserved, "spent": b.spent, "held": b.held,
                          "available": b.available, "expires_at": ledger.iso(b.expires_at),
                          "revoked_at": ledger.iso(b.revoked_at) if b.revoked_at else None,
                          "frozen_at": ledger.iso(b.frozen_at) if b.frozen_at else None,
                          "freeze_reason": b.freeze_reason})


@mcp.custom_route("/revoke/{block_id}", ["POST"])
async def revoke(request):
    """R4. Consent withdrawn: every later money call on this block refuses
    immediately, including one made mid-session."""
    block_id = request.path_params["block_id"]
    conn = ledger.connect()
    try:
        changed = ledger.revoke(conn, block_id)
    finally:
        conn.close()
    return JSONResponse({"block_id": block_id, "revoked": changed}, 200 if changed else 404)


@mcp.custom_route("/unfreeze/{block_id}", ["POST"])
async def unfreeze(request):
    block_id = request.path_params["block_id"]
    conn = ledger.connect()
    try:
        changed = ledger.unfreeze(conn, block_id)
    finally:
        conn.close()
    if changed is None:
        return JSONResponse({"error": "block not found"}, 404)
    return JSONResponse({"block_id": block_id, "unfrozen": changed})


@mcp.custom_route("/approve/{call_id}", ["POST"])
async def approve(request):
    """Release one HOLD, and only ever once: the pending call is popped, so a
    replayed or forged approval finds nothing to approve (B14)."""
    call_id = request.path_params["call_id"]
    pending = _HOLDS.pop(call_id, None)
    if pending is None:
        return JSONResponse({"error": "no call is waiting for approval under that id"}, 404)
    tool, args, ref = pending
    audit.record(event="hold_approved", call_id=call_id, tool=tool)
    conn = ledger.connect()
    try:
        if not ledger.renew_hold(conn, ref, config().reservation_ttl_minutes):
            return JSONResponse({"error": "this hold is no longer approvable: it expired,"
                                 " or its block was revoked, expired or frozen"}, 410)
        result = await _settle(conn, tool, ref, args)
    except ValueError as e:
        return JSONResponse({"approved": call_id, "error": str(e)}, 502)
    finally:
        conn.close()
    return JSONResponse({"approved": call_id, "result": result})


def bearer_auth(app):
    """Reject every HTTP request that does not present the right credential.

    Two secrets, not one. The agent holds RESERVE_GATE_TOKEN to reach /mcp;
    /approve, /revoke, /unfreeze and /block need RESERVE_GATE_ADMIN_TOKEN, which it is
    never given. Guarding the human approval gate with the token the gated party
    already holds would let the agent approve its own spending (G12). /health is
    open so an uptime check needs no credential at all.
    """
    async def wrapper(scope, receive, send):
        # This guard and Starlette's router have to read the same string. Serving
        # under a root path breaks that: Starlette strips the prefix before
        # routing while `scope["path"]` here still carries it, so /unfreeze/{id}
        # would stop matching ADMIN_PATHS and fall through to the agent's own
        # token. Never pass --root-path, or match on the stripped value instead.
        if scope["type"] == "http" and scope["path"] not in ("/health", "/webhook"):
            name = ("RESERVE_GATE_ADMIN_TOKEN" if scope["path"].startswith(ADMIN_PATHS)
                    else "RESERVE_GATE_TOKEN")
            expected = os.environ.get(name) or ""
            headers = dict(scope.get("headers") or [])
            presented = headers.get(b"authorization", b"").decode("latin-1")
            # compare_digest keeps the comparison time independent of how many
            # leading characters happen to match.
            if not expected or not secrets.compare_digest(presented, "Bearer " + expected):
                audit.record(event="auth_reject", path=scope["path"],
                             presented=bool(presented), required=name)
                return await JSONResponse({"error": "unauthorized"}, 401)(scope, receive, send)
            # The proxy forwards upstream with its own Razorpay credential. A
            # caller's header must not travel any further than this line.
            scope = dict(scope, headers=[(k, v) for k, v in (scope.get("headers") or [])
                                         if k != b"authorization"])
        await app(scope, receive, send)

    return wrapper


def main() -> None:
    p = argparse.ArgumentParser(prog="reserve-gate")
    p.add_argument("--http", action="store_true",
                   help="serve over HTTP instead of stdio (used by the deployment)")
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    a = p.parse_args()
    if not a.http:
        return mcp.run()
    for name in ("RESERVE_GATE_TOKEN", "RESERVE_GATE_ADMIN_TOKEN"):
        if not os.environ.get(name):
            raise SystemExit(f"{name} is not set. Refusing to serve a money endpoint without it.")
    # G12 is privilege separation, so equal values are one credential wearing two
    # names and the agent could approve its own spending.
    if os.environ["RESERVE_GATE_TOKEN"] == os.environ["RESERVE_GATE_ADMIN_TOKEN"]:
        raise SystemExit("RESERVE_GATE_TOKEN and RESERVE_GATE_ADMIN_TOKEN are equal."
                         " The admin routes would accept the agent's own credential.")
    import uvicorn
    # 0.0.0.0 is required: Render routes to the container's external interface,
    # and the bearer check above runs in front of every request.
    uvicorn.run(bearer_auth(mcp.streamable_http_app()),
                host="0.0.0.0", port=a.port)  # nosec B104


if __name__ == "__main__":
    main()
