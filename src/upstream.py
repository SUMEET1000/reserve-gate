"""The only place that knows how reserve-gate talks to Razorpay.

Everything else in the repo calls call_razorpay() and stays unaware of the
transport. Today that is Razorpay's hosted MCP server; swapping it for the
REST API at api.razorpay.com means rewriting this one function.
"""
import base64
import json
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

# Pinned in config and never taken from a request: a caller-supplied upstream
# URL would turn the proxy into an SSRF gadget holding a live payment key.
UPSTREAM_URL = "https://mcp.razorpay.com/mcp"


class UpstreamError(RuntimeError):
    """Razorpay refused the call or was unreachable.

    `known` separates the two, and G14 turns on the difference. Razorpay
    answering with a refusal proves the call did not happen, so the hold can go
    back to the block. A timeout or a dropped connection proves nothing —
    Razorpay may have captured and lost the reply — so releasing there would
    hand back a balance that was really spent and let the next call spend it
    again.
    """

    def __init__(self, message: str, *, known: bool = True):
        super().__init__(message)
        self.known = known


def _auth_header() -> str:
    """Read the credential at call time, not at import time, so that the tests
    and the scripted buyer run in a clone with no environment set."""
    try:
        kid, secret = os.environ["RAZORPAY_KEY_ID"], os.environ["RAZORPAY_KEY_SECRET"]
    except KeyError as e:
        raise UpstreamError(f"{e.args[0]} is not set. Copy .env.example to .env.") from None
    return "Basic " + base64.b64encode(f"{kid}:{secret}".encode()).decode()


async def call_razorpay(tool: str, args: dict) -> dict:
    """Forward one tool call upstream and return the parsed reply.

    The proxy's own credential is the only one sent. A caller's Authorization
    header never reaches here; see the auth wrapper in server.py.
    """
    # ponytail: one session per call. Razorpay's hosted server is stateless for
    # our four tools and a demo is a handful of calls; pool it if latency shows.
    try:
        async with streamablehttp_client(
            UPSTREAM_URL, headers={"Authorization": _auth_header()}
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, args)
    except UpstreamError:
        raise
    except Exception as e:
        # Transport, TLS, timeout, protocol. None of them say whether Razorpay
        # acted, so the outcome is unknown and the caller must not release.
        raise UpstreamError(f"{type(e).__name__}: {e}", known=False) from e

    text = "".join(c.text for c in result.content if getattr(c, "text", None))
    if result.isError:
        raise UpstreamError(text or "upstream returned an error with no message")
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        # Not every tool replies with JSON. Hand back the text rather than fail.
        return {"text": text}


if __name__ == "__main__":
    import asyncio

    async def probe():
        try:
            print(await call_razorpay("fetch_all_orders", {"count": 1}))
        except UpstreamError as e:
            print("UpstreamError:", e)

    asyncio.run(probe())
