"""The decision, and nothing else.

decide() is handed a call, a snapshot of the ledger and the clock, and returns
ALLOW, BLOCK or HOLD together with the rule that fired. It performs no I/O,
opens no connection, reads no clock and consults no model, so a test can sit it
exactly on a boundary and a refusal can be explained without replaying anything.

Storage, transactions and the audit log live in ledger.py.
"""
from dataclasses import dataclass, field
from datetime import datetime

import yaml

ALLOW, BLOCK, HOLD = "ALLOW", "BLOCK", "HOLD"

# The tools the gate will judge. Anything else is refused by name rather than
# forwarded, so a Razorpay tool invented after this was written arrives blocked.
MONEY_TOOLS = ("create_order", "capture_payment")

# Razorpay refuses an order below 1.00 in the currency's main unit, so an amount
# under this would be ALLOWed here and then 400 upstream, which reads in the eval
# report as the gate letting a bad call through.
MIN_AMOUNT = 100
# Far below the point where SQLite refuses an integer (10**19 raises OverflowError
# inside the transaction, after the decision has already been taken).
MAX_AMOUNT = 10 ** 9


@dataclass(frozen=True)
class Config:
    reserved: int
    currency: str
    expires_days: int
    max_txn: int
    approval_over: int
    velocity_calls: int
    velocity_window_minutes: int
    reservation_ttl_minutes: int
    derived_key_ttl_seconds: int


def load_config(path: str = "policy.yaml") -> Config:
    """Read policy.yaml into a Config. A missing or misspelled key raises here,
    at startup, rather than at the moment a money call needs it."""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    config = Config(**raw["block"], **raw["rules"])
    if config.approval_over >= config.max_txn:
        raise ValueError(
            f"approval_over ({config.approval_over}) must be below max_txn ({config.max_txn}),"
            " or R5 refuses every call that would otherwise need approval and HOLD is dead code")
    if config.max_txn > config.reserved:
        raise ValueError(f"max_txn ({config.max_txn}) is larger than the block ({config.reserved})")
    if not 1 <= config.expires_days <= 90:
        raise ValueError(f"expires_days must be 1..90, got {config.expires_days}")
    return config


@dataclass(frozen=True)
class Call:
    tool: str
    caller_id: str
    amount: int | None = None
    currency: str | None = None
    order_id: str | None = None   # capture_payment only
    payment_id: str | None = None
    idem_key: str | None = None


@dataclass(frozen=True)
class Block:
    block_id: str
    caller_id: str
    currency: str
    reserved: int
    spent: int          # committed: a payment was captured
    held: int           # reserved against an order that is not captured yet
    expires_at: datetime
    revoked_at: datetime | None = None
    frozen_at: datetime | None = None
    freeze_reason: str | None = None

    @property
    def available(self) -> int:
        return self.reserved - self.spent - self.held


@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    block_id: str
    amount: int
    currency: str
    state: str          # held | committed | released
    expires_at: datetime
    order_id: str | None = None
    payment_id: str | None = None


@dataclass(frozen=True)
class State:
    """Everything decide() is allowed to know. Assembled by ledger.py inside the
    transaction, so nothing here can change while the decision is being taken."""
    block: Block | None = None
    velocity_count: int = 0
    replay: dict | None = None        # R7: this key already completed
    in_flight: bool = False           # R7: this key is running right now
    conflict: bool = False            # G16: same key, different parameters
    reservation: Reservation | None = None   # capture: the order being captured


@dataclass(frozen=True)
class Decision:
    outcome: str
    rule: str
    reason: str
    detail: dict = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.outcome == ALLOW


def decide(call: Call, state: State, config: Config, now: datetime) -> Decision:
    """ALLOW, BLOCK or HOLD, with the rule that decided it.

    `now` is a parameter and never a call to the clock, so expiry can be tested
    on the instant rather than around it.
    """
    try:
        return _decide(call, state, config, now)
    except Exception as e:
        # G4. A gate that fails open is not a gate: a malformed argument, a bad
        # comparison or an outright bug all end here, as a refusal.
        return Decision(BLOCK, "G4", f"decision failed closed: {type(e).__name__}: {e}")


def _decide(call: Call, state: State, config: Config, now: datetime) -> Decision:
    if call.tool not in MONEY_TOOLS:
        return Decision(BLOCK, "G15", f"{call.tool!r} is not a gated money tool")

    # R7 and G16 come first: a completed call must replay its first result
    # without re-running any rule, and a key reused with different parameters is
    # a conflict rather than a second success.
    if state.conflict:
        return Decision(BLOCK, "G16", "idempotency key already used with different parameters",
                        {"idem_key": call.idem_key})
    if state.in_flight:
        return Decision(BLOCK, "R7", "a call with this idempotency key is still running",
                        {"idem_key": call.idem_key})
    if state.replay is not None:
        # The stored reply rides along with the decision. Detecting the replay
        # and then dropping the first result leaves the retry indistinguishable
        # from a fresh success, which is the duplicate R7 exists to prevent.
        return Decision(ALLOW, "R7", "replay of a completed call",
                        {"idem_key": call.idem_key, "replay": True, "result": state.replay})

    block = state.block
    if block is None:
        return Decision(BLOCK, "R1", "no spending block exists for this caller")
    # G2. The block id is a server-side fact bound to the caller, never a handle
    # the caller can present.
    if block.caller_id != call.caller_id:
        return Decision(BLOCK, "G2", "block belongs to a different caller")
    if block.frozen_at is not None:
        return Decision(BLOCK, "G4", "block frozen after a reconciliation conflict",
                        {"block_id": block.block_id, "frozen_at": block.frozen_at.isoformat(),
                         "reason": block.freeze_reason})
    if block.revoked_at is not None:
        return Decision(BLOCK, "R4", "block revoked",
                        {"block_id": block.block_id, "revoked_at": block.revoked_at.isoformat()})
    # Documented boundary: the expiry instant itself refuses.
    if now >= block.expires_at:
        return Decision(BLOCK, "R2", "block expired",
                        {"block_id": block.block_id, "expires_at": block.expires_at.isoformat()})

    if state.velocity_count >= config.velocity_calls:
        return Decision(BLOCK, "R6",
                        f"more than {config.velocity_calls} money calls in "
                        f"{config.velocity_window_minutes} minutes",
                        {"count": state.velocity_count})

    if call.tool == "capture_payment":
        return _decide_capture(call, state, block, now)
    return _decide_order(call, block, config)


def _check_amount(amount, currency, expected_currency: str) -> Decision | None:
    """R0. Returns a refusal, or None when the amount and currency are usable.

    The type check matters to the harness and to any caller that hands decide()
    a raw JSON value. It cannot fire on the MCP path: FastMCP validates against
    the tool signature with pydantic first, so True arrives as 1 and "50000" as
    50000, both genuine ints (measured 28 Aug 2026). The bounds are what stop
    those. `type() is not int` rather than isinstance, because a bool is an int.
    """
    if type(amount) is not int:
        return Decision(BLOCK, "R0", f"amount must be an integer, got {type(amount).__name__}",
                        {"amount": repr(amount)})
    if not MIN_AMOUNT <= amount <= MAX_AMOUNT:
        return Decision(BLOCK, "R0", f"amount {amount} is outside {MIN_AMOUNT}..{MAX_AMOUNT}",
                        {"amount": amount})
    if not isinstance(currency, str) or currency.upper() != expected_currency.upper():
        return Decision(BLOCK, "R0",
                        f"currency {currency!r} does not match {expected_currency!r}",
                        {"currency": currency, "expected": expected_currency})
    return None


def _decide_order(call: Call, block: Block, config: Config) -> Decision:
    bad = _check_amount(call.amount, call.currency, block.currency)
    if bad:
        return bad

    amount = call.amount
    if amount > config.max_txn:
        return Decision(BLOCK, "R5", f"amount {amount} is over the per-call cap {config.max_txn}",
                        {"amount": amount, "max_txn": config.max_txn})
    if amount > block.available:
        return Decision(BLOCK, "R1",
                        f"amount {amount} is over the {block.available} left in the block",
                        {"amount": amount, "available": block.available,
                         "reserved": block.reserved, "spent": block.spent, "held": block.held})
    # After R1, so a hold is only ever raised for a call the block could pay.
    if amount > config.approval_over:
        return Decision(HOLD, "approval",
                        f"amount {amount} is over {config.approval_over} and needs a human",
                        {"amount": amount, "approval_over": config.approval_over})
    return Decision(ALLOW, "", "within the block",
                    {"amount": amount, "available_after": block.available - amount})


def _decide_capture(call: Call, state: State, block: Block, now: datetime) -> Decision:
    """Capture commits a reservation that create_order already took.

    No amount rule is applied here. Razorpay requires the capture amount to
    equal the authorized amount, which came from an order R1 and R5 already
    gated, so charging it again would debit the block twice for one purchase.
    """
    res = state.reservation
    if res is None:
        return Decision(BLOCK, "R3", "no reservation for this order; it was not created here",
                        {"order_id": call.order_id})
    # G2. An order_id is a handle, not proof of ownership: it is Razorpay's id
    # and anyone who saw the order has it. Capturing it must debit the block that
    # reserved for it, so a reservation belonging to another block is refused
    # here as well as being invisible to the ledger's own scoped lookup.
    if res.block_id != block.block_id:
        return Decision(BLOCK, "G2", "that order was reserved against a different block",
                        {"order_id": call.order_id})
    if res.state == "committed":
        return Decision(BLOCK, "R3", "this order was already captured",
                        {"order_id": call.order_id})
    if res.state == "released":
        return Decision(BLOCK, "R3", "the reservation for this order was already released",
                        {"order_id": call.order_id})
    if now >= res.expires_at:
        return Decision(BLOCK, "R3", "the reservation expired and its amount returned to the block",
                        {"order_id": call.order_id, "expires_at": res.expires_at.isoformat()})
    if type(call.amount) is not int or call.amount != res.amount:
        return Decision(BLOCK, "R0", "capture amount does not equal the reserved amount",
                        {"amount": repr(call.amount), "reserved_amount": res.amount})
    if not isinstance(call.currency, str) or call.currency.upper() != res.currency.upper():
        return Decision(BLOCK, "R0", f"capture currency {call.currency!r} does not match "
                                     f"the reservation's {res.currency!r}")
    if res.payment_id is not None and call.payment_id != res.payment_id:
        return Decision(BLOCK, "R0", "capture payment id does not match the reservation")
    return Decision(ALLOW, "", "commits a held reservation",
                    {"order_id": call.order_id, "amount": res.amount,
                     "reservation_id": res.reservation_id})
