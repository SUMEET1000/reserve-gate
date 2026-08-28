"""The AI buyer — the other side of the gate.

It reaches reserve-gate over MCP and shops. It cannot see the block, the rules
or the balance: it finds out what it may spend by being refused, and the refusal
tells it which rule fired and what is left.

    --scripted   a fixed basket, no model and no model key. This is what the
                 harness and every number in the eval report run on, because a
                 measurement taken through a model is not repeatable.
    --overspend  four calls that are refused by policy alone. No network, no
                 Razorpay key: every one is decided before anything leaves the
                 machine, so a fresh clone can run it.
    --llm        a real model proposes the calls. The demo beat, not the
                 measurement. Needs GEMINI_API_KEY and requirements-llm.txt.

The server is launched over stdio, so this whole loop is self-contained.
"""
import argparse
import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Amounts are paise. policy.yaml gives a 1,000,000 block, a 500,000 per-call cap
# and a 200,000 approval line, and these baskets are written against those three
# numbers — change the policy and the expected outcomes below move with it.
BASKETS = {
    # Five purchases the block can pay for, then one it cannot. The last line is
    # the point: the refusal names R1 and prints what is actually left.
    "scripted": [
        ("noise-cancelling headphones", {"amount": 180000, "currency": "INR"}),
        ("mechanical keyboard", {"amount": 150000, "currency": "INR"}),
        ("monitor arm", {"amount": 200000, "currency": "INR"}),
        ("desk lamp", {"amount": 190000, "currency": "INR"}),
        ("laptop stand", {"amount": 175000, "currency": "INR"}),
        ("second monitor arm — past the block", {"amount": 200000, "currency": "INR"}),
    ],
    # Every one of these is decided before any upstream call, so this basket
    # runs with no Razorpay key and no network at all. The two holds are what
    # make the last line reachable offline: a hold encumbers the block while it
    # waits for a human, so the sixth call is refused against a balance that
    # nothing has actually spent yet.
    "overspend": [
        ("a television, over the per-call cap", {"amount": 600000, "currency": "INR"}),
        ("a coin, under Razorpay's minimum", {"amount": 50, "currency": "INR"}),
        ("dollars against a rupee block", {"amount": 50000, "currency": "USD"}),
        ("a laptop, over the approval line", {"amount": 500000, "currency": "INR"}),
        ("a phone, also over it", {"amount": 400000, "currency": "INR"}),
        ("a printer — nothing left to spend", {"amount": 200000, "currency": "INR"}),
    ],
}

# What the model is told. It is given no numbers: discovering the bounds by
# being refused is the behaviour the demo is showing.
LLM_PROMPT = (
    "You are buying office equipment through the reserve_gate tools. Buy a"
    " mechanical keyboard for 150000 paise, then a television for 600000 paise."
    " Call create_order once per item with currency INR and a distinct"
    " idempotency_key. If a call is refused, say which rule refused it and stop."
)


def text_of(result) -> str:
    return "".join(c.text for c in result.content if getattr(c, "text", None))


async def shop(session: ClientSession, basket) -> int:
    """Place every order in the basket. Returns how many were refused."""
    refused = 0
    for i, (label, args) in enumerate(basket):
        print(f"\n> {label}")
        print(f"  create_order {args['amount']} {args['currency']}")
        result = await session.call_tool(
            "create_order", {**args, "idempotency_key": f"basket-{i}-{label[:20]}"})
        body = text_of(result)
        if result.isError:
            refused += 1
            print(f"  REFUSED   {body}")
        else:
            print(f"  ALLOWED   {body[:300]}")
    return refused


async def shop_with_llm(session: ClientSession) -> None:
    """One model turn with the gate's tools attached.

    Imported here and not at module scope: --scripted must run in a clone with
    no model SDK installed, which is the whole reason it is the primary mode.
    """
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    response = await client.aio.models.generate_content(
        model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        contents=LLM_PROMPT,
        # temperature 0 because the video is timed to the second and a model at
        # default temperature adds a preamble or picks a different tool.
        config=types.GenerateContentConfig(temperature=0, tools=[session]),
    )
    print(response.text)


async def run(mode: str, db: str | None) -> None:
    env = dict(os.environ)
    if db:
        env["RESERVE_GATE_DB"] = db
    params = StdioServerParameters(command=sys.executable, args=["-m", "src.server"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print(f"connected to reserve_gate — {len((await session.list_tools()).tools)}"
                  " tools offered\n")
            if mode == "llm":
                return await shop_with_llm(session)
            refused = await shop(session, BASKETS[mode])
            print(f"\n{refused} of {len(BASKETS[mode])} calls refused by the gate.")


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m src.buyer", description=__doc__)
    p.add_argument("--scripted", action="store_true", help="fixed basket, no model")
    p.add_argument("--overspend", action="store_true",
                   help="calls refused by policy alone; needs no key and no network")
    p.add_argument("--llm", action="store_true", help="a real model proposes the calls")
    p.add_argument("--db", help="ledger file to use (default: reserve_gate.db)")
    a = p.parse_args()
    if a.llm:
        mode = "llm"
    elif a.overspend:
        mode = "overspend"
    else:
        mode = "scripted"
    asyncio.run(run(mode, a.db))


if __name__ == "__main__":
    main()
