"""reserve-gate: an MCP server that sits between an AI buyer and Razorpay.

Four tools are declared here by hand. That declaration *is* the allowlist:
a Razorpay tool that is not written below cannot be reached through this
server, including any tool Razorpay adds after this was written.

Today every call is forwarded and logged. The policy engine lands next.
"""
import argparse
import os
import secrets
from typing import Any

from starlette.responses import JSONResponse

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from . import audit
from .upstream import UpstreamError, call_razorpay


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


async def _forward(tool: str, args: dict, money: bool) -> Any:
    """Log the attempt, call Razorpay, log the outcome.

    `money` marks the calls the policy engine will gate; reads are recorded at
    a lower level so the audit trail shows the scope of the gate honestly.
    """
    kind = "money" if money else "read"
    try:
        result = await call_razorpay(tool, args)
    except UpstreamError as e:
        audit.record(event="upstream_error", kind=kind, tool=tool, args=args, error=str(e))
        # Surfaced as a tool result with isError set, not a protocol error, so
        # the model reads the reason instead of the client retrying blindly.
        raise ValueError(f"{tool} failed upstream: {e}") from None
    audit.record(event="allow", kind=kind, tool=tool, args=args,
                 upstream_id=result.get("id"), status=result.get("status"))
    return result


@mcp.tool()
async def create_order(amount: int, currency: str = "INR", receipt: str | None = None,
                       notes: dict[str, str] | None = None) -> Any:
    """Create a Razorpay order. `amount` is in paise, the smallest unit of the
    currency, so 50000 means 500.00 rupees."""
    args: dict[str, Any] = {"amount": amount, "currency": currency}
    if receipt:
        args["receipt"] = receipt
    if notes:
        args["notes"] = notes
    return await _forward("create_order", args, money=True)


@mcp.tool()
async def capture_payment(payment_id: str, amount: int, currency: str = "INR") -> Any:
    """Capture an authorized payment. `amount` is in paise and must equal the
    authorized amount."""
    return await _forward(
        "capture_payment",
        {"payment_id": payment_id, "amount": amount, "currency": currency},
        money=True,
    )


@mcp.tool()
async def fetch_order(order_id: str) -> Any:
    """Read one order by id."""
    return await _forward("fetch_order", {"order_id": order_id}, money=False)


@mcp.tool()
async def fetch_payment(payment_id: str) -> Any:
    """Read one payment by id."""
    return await _forward("fetch_payment", {"payment_id": payment_id}, money=False)


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


def bearer_auth(app):
    """Reject every HTTP request that does not present the gate token.

    The MCP specification requires a server on an HTTP transport to verify
    inbound requests; without this the deployed URL would let anyone who finds
    it move money on the merchant's account. /health is left open so an uptime
    check does not need the credential.
    """
    expected = "Bearer " + (os.environ.get("RESERVE_GATE_TOKEN") or "")

    async def wrapper(scope, receive, send):
        if scope["type"] == "http" and scope["path"] != "/health":
            headers = dict(scope.get("headers") or [])
            presented = headers.get(b"authorization", b"").decode("latin-1")
            # compare_digest keeps the comparison time independent of how many
            # leading characters happen to match.
            if not os.environ.get("RESERVE_GATE_TOKEN") or \
                    not secrets.compare_digest(presented, expected):
                audit.record(event="auth_reject", path=scope["path"],
                             presented=bool(presented))
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
    if not os.environ.get("RESERVE_GATE_TOKEN"):
        raise SystemExit("RESERVE_GATE_TOKEN is not set. Refusing to serve an "
                         "unauthenticated money endpoint.")
    import uvicorn
    uvicorn.run(bearer_auth(mcp.streamable_http_app()), host="0.0.0.0", port=a.port)


if __name__ == "__main__":
    main()
