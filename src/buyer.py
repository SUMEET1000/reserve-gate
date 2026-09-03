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
    --live       one purchase carried all the way to settlement, so the block
                 shows money spent and not only money held. Needs a Razorpay
                 test key and a person to pay in a browser.

The server is launched over stdio, so this whole loop is self-contained.
"""
import argparse
import asyncio
import json
import os
import pathlib
import secrets
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# The shop's shelf is web/catalogue.json and the agent's basket is the `basket`
# field in it, so the page a judge browses and the run the eval numbers came from
# cannot drift apart. Amounts are paise. policy.yaml gives a 1,000,000 block, a
# 500,000 per-call cap and a 200,000 approval line, and the basket is written
# against those three numbers - change either file and the outcomes move.
CATALOGUE = json.loads(
    (pathlib.Path(__file__).resolve().parent.parent / "web" / "catalogue.json")
    .read_text(encoding="utf-8"))


def basket_from_catalogue() -> list[tuple[str, dict]]:
    """Five purchases the block can pay for, then one it cannot. The last line
    is the point: the refusal names R1 and prints what is actually left."""
    shopping = sorted((i for i in CATALOGUE["items"] if i.get("basket")),
                      key=lambda i: i["basket"])
    return [(i["name"], {"amount": i["paise"], "currency": CATALOGUE["currency"]})
            for i in shopping]


BASKETS = {
    "scripted": basket_from_catalogue(),
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


def receipt_for(label: str, run_id: str) -> str:
    """Turn a basket label into a Razorpay receipt.

    Razorpay caps `receipt` at 40 ASCII characters (e5), and the baskets carry
    an em dash, so the label is transliterated before it is cut. The run token
    is what keeps a second run from re-sending a receipt the account has already
    seen, which the API is documented to treat as a duplicate.
    """
    ascii_label = label.encode("ascii", "replace").decode("ascii").replace("?", "-")
    return f"{ascii_label[:33]}-{run_id}"


async def shop(session: ClientSession, basket) -> int:
    """Place every order in the basket. Returns how many were refused."""
    refused = 0
    run_id = secrets.token_urlsafe(4)
    for i, (label, args) in enumerate(basket):
        print(f"\n> {label}")
        print(f"  create_order {args['amount']} {args['currency']}")
        # The label rides along as the receipt so the order, and the audit
        # record beside it, name the thing being bought instead of an amount on
        # its own. It is never read by the policy: `Call` has no field for it.
        result = await session.call_tool(
            "create_order", {**args, "receipt": receipt_for(label, run_id),
                             "idempotency_key": f"basket-{i}-{label[:20]}"})
        body = text_of(result)
        if result.isError:
            refused += 1
            print(f"  REFUSED   {body}")
        else:
            print(f"  ALLOWED   {body[:300]}")
    return refused


async def shop_live(session: ClientSession, db: str | None) -> None:
    """One real purchase, start to finish, on one audit chain.

    The scripted baskets stop at create_order, so the block only ever shows
    money held and never money spent. This mode carries a single item through
    to settlement: order here, payment in a browser because Razorpay's API
    cannot make one, then capture back through the gate. All of it inside one
    session, so the records form one unbroken hash chain rather than three runs
    stitched together afterwards.

    ~1 rupee, so a repeat take costs nothing and stays under the approval line.
    """
    label, args = "a live test purchase", {"amount": 10000, "currency": "INR"}
    run_id = secrets.token_urlsafe(4)

    print(f"> {label}\n  create_order {args['amount']} {args['currency']}")
    result = await session.call_tool(
        "create_order", {**args, "receipt": receipt_for(label, run_id),
                         "idempotency_key": f"live-{run_id}"})
    if result.isError:
        print(f"  REFUSED   {text_of(result)}")
        return
    body = text_of(result)
    print(f"  ALLOWED   {body[:300]}")

    try:
        order_id = json.loads(body)["id"]
    except (ValueError, KeyError, TypeError):
        print("\nCould not read an order id out of that reply. Stopping before"
              " the capture rather than guessing one.")
        return

    print(f"\n  order id: {order_id}\n")
    print("Now pay it. Razorpay's API cannot make a payment; a person has to.")
    print("  1. open demo/pay.html in a browser")
    print("  2. paste the rzp_test_ key id, and the order id above")
    print("  3. pay with the domestic card 4100 2800 0000 1007, any future expiry, any CVV")
    print("     4111 1111 1111 1111 is an international card and is declined unless")
    print("     the account has enabled international payments")
    print("  4. the page prints a payment_id when the payment is authorized")

    payment_id = input("\npayment_id: ").strip()
    if not payment_id:
        print("Nothing entered. The reservation stays held until its TTL returns it.")
        return

    print(f"\n> capture {payment_id}")
    captured = await session.call_tool(
        "capture_payment", {"payment_id": payment_id, "amount": args["amount"],
                            "currency": args["currency"],
                            "idempotency_key": f"live-capture-{run_id}"})
    print(f"  {'REFUSED ' if captured.isError else 'CAPTURED'}  {text_of(captured)[:400]}")

    # Read straight from the ledger rather than reporting what the capture call
    # returned: the number that matters is the one the block now holds. Reached
    # through this payment's own reservation, because a ledger that has served
    # more than one caller holds more than one block, and an unscoped read here
    # printed a leftover block's untouched balance over a settlement that had
    # actually committed (29 Aug 2026).
    from . import ledger
    conn = ledger.connect(db)
    try:
        row = conn.execute(
            "SELECT b.reserved, b.spent, b.held FROM blocks b"
            " JOIN reservations r ON r.block_id = b.block_id"
            " WHERE r.payment_id = ?", (payment_id,)).fetchone()
    finally:
        conn.close()
    if row:
        print(f"\nblock: reserved {row['reserved']}  spent {row['spent']}"
              f"  held {row['held']}")
    else:
        print("\nNo block found for that payment. Read audit.jsonl before trusting this run.")


def print_turns(history) -> None:
    """Print the tool calls the SDK made on the model's behalf.

    Automatic function calling loops inside a single generate_content, so the
    response text alone is the model's closing sentence and nothing else. A
    cold reader of this repo counted one turn and concluded there was no loop
    (29 Aug 2026). The refusals are the demo, so they are printed here.
    """
    for content in history or []:
        for part in content.parts or []:
            if part.function_call:
                args = part.function_call.args or {}
                print(f"\n> {part.function_call.name}"
                      f" {args.get('amount')} {args.get('currency')}")
            if part.function_response:
                print(f"  gate: {str(part.function_response.response)[:300]}")


async def shop_with_llm(session: ClientSession) -> None:
    """A real model proposes the calls, and the gate answers each one.

    Imported here and not at module scope: --scripted must run in a clone with
    no model SDK installed, which is the whole reason it is the primary mode.
    """
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    response = await client.aio.models.generate_content(
        # gemini-2.5-flash was the default until 29 Aug 2026, when it started
        # returning 404 "no longer available to new users" on a fresh key. It is
        # still listed by ListModels, so the listing is not proof of access —
        # only a real generateContent call is. Re-check before recording.
        model=os.environ.get("GEMINI_MODEL", "gemini-3.6-flash"),
        contents=LLM_PROMPT,
        config=types.GenerateContentConfig(
            # temperature 0 because the video is timed to the second and a model
            # at default temperature adds a preamble or picks a different tool.
            temperature=0, tools=[session],
            # 10 is also the SDK's own default. It is set here so the ceiling on
            # a refused model's retries is visible in this file rather than in
            # someone else's package.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                maximum_remote_calls=10),
        ),
    )
    print_turns(response.automatic_function_calling_history)
    print(f"\n{response.text}")


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
            if mode == "live":
                return await shop_live(session, db)
            refused = await shop(session, BASKETS[mode])
            print(f"\n{refused} of {len(BASKETS[mode])} calls refused by the gate.")


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m src.buyer", description=__doc__)
    p.add_argument("--scripted", action="store_true", help="fixed basket, no model")
    p.add_argument("--overspend", action="store_true",
                   help="calls refused by policy alone; needs no key and no network")
    p.add_argument("--llm", action="store_true", help="a real model proposes the calls")
    p.add_argument("--live", action="store_true",
                   help="one purchase carried through to settlement; needs a browser payment")
    p.add_argument("--db", help="ledger file to use (default: reserve_gate.db)")
    a = p.parse_args()
    if a.llm:
        mode = "llm"
    elif a.live:
        mode = "live"
    elif a.overspend:
        mode = "overspend"
    else:
        mode = "scripted"
    if mode == "overspend" and not a.db:
        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(run(mode, os.path.join(directory, "reserve_gate.db")))
    else:
        asyncio.run(run(mode, a.db))


if __name__ == "__main__":
    main()
