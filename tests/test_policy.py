"""decide() on its own: no database, no clock, no network.

Rule ids in the test names are from §5 of the build plan; B-numbers are the
bypass table in §11.2.
"""
from datetime import datetime, timedelta, timezone

import pytest

from src.policy import (ALLOW, BLOCK, HOLD, Block, Call, Config, Reservation, State,
                        decide, load_config)

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
CALLER = "caller01"

CFG = Config(reserved=1000000, currency="INR", expires_days=30, max_txn=500000,
             approval_over=200000, velocity_calls=10, velocity_window_minutes=1,
             reservation_ttl_minutes=15, derived_key_ttl_seconds=300)


def block(**over) -> Block:
    base = dict(block_id="blk1", caller_id=CALLER, currency="INR", reserved=1000000,
                spent=0, held=0, expires_at=NOW + timedelta(days=30), revoked_at=None)
    return Block(**{**base, **over})


def order(amount=50000, currency="INR", caller=CALLER) -> Call:
    return Call(tool="create_order", caller_id=caller, amount=amount, currency=currency)


def run(call, **state_kw):
    state = State(block=state_kw.pop("block", block()), **state_kw)
    return decide(call, state, CFG, NOW)


def test_a_normal_order_is_allowed():
    d = run(order(50000))
    assert d.outcome == ALLOW, d
    assert d.detail["available_after"] == 950000


# R0 -------------------------------------------------------------------------

@pytest.mark.parametrize("amount", [True, False, 1000.7, 1e9, "50000", None, [100]])
def test_r0_refuses_anything_that_is_not_an_int(amount):
    """B04, e1. A bool is an int to isinstance, so the check is `type() is not int`."""
    d = run(order(amount))
    assert d.outcome == BLOCK and d.rule == "R0", d


@pytest.mark.parametrize("amount", [-1, 0, 99, 10 ** 9 + 1, 10 ** 19])
def test_r0_refuses_amounts_outside_the_bounds(amount):
    """B04, B05, e3, e4. 99 is below Razorpay's own minimum order; 10**19 would
    raise OverflowError inside the transaction if it got that far."""
    d = run(order(amount))
    assert d.outcome == BLOCK and d.rule == "R0", d


def test_r0_accepts_the_exact_lower_bound():
    assert run(order(100)).outcome == ALLOW


@pytest.mark.parametrize("currency", ["USD", "JPY", "XXX", "", "IN", None, 1])
def test_r0_refuses_a_currency_the_block_does_not_hold(currency):
    """E1. Without this, 1000000 'USD' passes a 10,000-rupee block as US$10,000."""
    d = run(order(1000000, currency))
    assert d.outcome == BLOCK and d.rule == "R0", d


def test_r0_matches_currency_case_insensitively():
    assert run(order(50000, "inr")).outcome == ALLOW


# R1, R5, approval -----------------------------------------------------------

def test_r5_refuses_a_call_over_the_per_call_cap():
    d = run(order(500001))
    assert d.outcome == BLOCK and d.rule == "R5", d


def test_r1_counts_held_as_well_as_spent():
    """A reservation that has not been captured still consumes the block."""
    d = run(order(300000), block=block(spent=400000, held=400000))
    assert d.outcome == BLOCK and d.rule == "R1", d
    assert d.detail["available"] == 200000


def test_r1_allows_an_amount_that_exactly_fills_the_block():
    """B06. The boundary is inclusive, and one sub-unit over is not."""
    assert run(order(100000), block=block(spent=900000)).outcome == ALLOW
    assert run(order(100001), block=block(spent=900000)).rule == "R1"


def test_a_large_call_holds_for_a_human():
    d = run(order(200001))
    assert d.outcome == HOLD and d.rule == "approval", d


def test_the_approval_boundary_itself_does_not_hold():
    assert run(order(200000)).outcome == ALLOW


def test_a_hold_that_the_block_cannot_pay_is_a_block_not_a_hold():
    d = run(order(300000), block=block(spent=900000))
    assert d.outcome == BLOCK and d.rule == "R1", d


# R2, R4, R6 -----------------------------------------------------------------

def test_r4_refuses_every_call_on_a_revoked_block():
    d = run(order(100), block=block(revoked_at=NOW - timedelta(seconds=1)))
    assert d.outcome == BLOCK and d.rule == "R4", d


def test_r2_refuses_on_the_expiry_instant_itself():
    """B10. The documented boundary is `now >= expires_at`, so the instant refuses."""
    assert run(order(100), block=block(expires_at=NOW)).rule == "R2"
    assert run(order(100), block=block(expires_at=NOW + timedelta(microseconds=1))).outcome == ALLOW


def test_r6_refuses_once_the_window_is_full():
    """B24, OWASP LLM06:2026. The count is at the cap, so the next call is over it."""
    d = run(order(100), velocity_count=10)
    assert d.outcome == BLOCK and d.rule == "R6", d
    assert run(order(100), velocity_count=9).outcome == ALLOW


# G-rules --------------------------------------------------------------------

@pytest.mark.parametrize("tool", ["create_refund", "create_payment_link", "create_qr_code",
                                  "some_tool_razorpay_ships_next_year"])
def test_g15_refuses_any_tool_that_is_not_a_gated_money_tool(tool):
    """B16, B28. Default-deny: a tool nobody wrote is refused, not forwarded."""
    d = decide(Call(tool=tool, caller_id=CALLER, amount=100, currency="INR"),
               State(block=block()), CFG, NOW)
    assert d.outcome == BLOCK and d.rule == "G15", d


def test_g2_refuses_another_callers_block():
    """B02. A block is bound to its caller server-side, never presented as a handle."""
    d = run(order(caller="someone_else"))
    assert d.outcome == BLOCK and d.rule == "G2", d


def test_no_block_at_all_is_a_refusal():
    d = run(order(), block=None)
    assert d.outcome == BLOCK and d.rule == "R1", d


def test_g4_fails_closed_when_the_decision_itself_breaks():
    """B20. A gate that fails open is not a gate, so a broken state is a refusal."""
    class Exploding:
        def __getattr__(self, name):
            raise RuntimeError("ledger handed us nonsense")

    d = decide(order(100), Exploding(), CFG, NOW)
    assert d.outcome == BLOCK and d.rule == "G4", d


# R7, G16 --------------------------------------------------------------------

def test_r7_replays_the_first_result_without_re_running_any_rule():
    """B08. The replayed call is over the block and still returns the first result,
    which proves no rule ran a second time."""
    d = run(order(999999999), block=block(spent=1000000), replay={"id": "order_ABC"})
    assert d.outcome == ALLOW and d.rule == "R7", d


def test_g16_refuses_a_key_reused_with_different_parameters():
    """B27. Otherwise an attacker registers a key against a 1-rupee call and the
    victim's real call returns that call's 'success'."""
    d = run(order(50000), conflict=True)
    assert d.outcome == BLOCK and d.rule == "G16", d


def test_a_key_still_running_is_refused_rather_than_replayed():
    d = run(order(50000), in_flight=True)
    assert d.outcome == BLOCK and d.rule == "R7", d


# capture --------------------------------------------------------------------

def reservation(**over) -> Reservation:
    base = dict(reservation_id="res1", block_id="blk1", amount=50000, currency="INR",
                state="held", expires_at=NOW + timedelta(minutes=15), order_id="order_X")
    return Reservation(**{**base, **over})


def capture(amount=50000, currency="INR", order_id="order_X") -> Call:
    return Call(tool="capture_payment", caller_id=CALLER, amount=amount,
                currency=currency, order_id=order_id)


def test_capture_commits_a_held_reservation():
    d = run(capture(), reservation=reservation())
    assert d.outcome == ALLOW, d
    assert d.detail["reservation_id"] == "res1"


def test_capture_refuses_an_order_the_gate_never_created():
    """E2. The payment may be real, but nothing here reserved for it."""
    d = run(capture(), reservation=None)
    assert d.outcome == BLOCK and d.rule == "R3", d


@pytest.mark.parametrize("state", ["committed", "released"])
def test_capture_refuses_a_reservation_that_is_already_closed(state):
    """A second capture of one order must not debit the block twice."""
    d = run(capture(), reservation=reservation(state=state))
    assert d.outcome == BLOCK and d.rule == "R3", d


def test_capture_refuses_after_the_reservation_ttl():
    d = run(capture(), reservation=reservation(expires_at=NOW))
    assert d.outcome == BLOCK and d.rule == "R3", d


def test_capture_refuses_an_amount_that_is_not_the_reserved_one():
    d = run(capture(amount=60000), reservation=reservation())
    assert d.outcome == BLOCK and d.rule == "R0", d


def test_capture_refuses_a_reservation_taken_against_another_block():
    """G2. An order id is Razorpay's handle, not proof of ownership — anyone who
    saw the order has it. Capturing it must debit the block that reserved for it,
    so presenting it against a different block is refused rather than honoured."""
    d = run(capture(), reservation=reservation(block_id="someone_elses_block"))
    assert d.outcome == BLOCK and d.rule == "G2", d


def test_capture_is_refused_on_a_revoked_block():
    """B12. Revocation reaches a call whose reservation is already open."""
    d = run(capture(), block=block(revoked_at=NOW), reservation=reservation())
    assert d.outcome == BLOCK and d.rule == "R4", d


# config ---------------------------------------------------------------------

def test_the_shipped_policy_file_loads_and_is_self_consistent():
    cfg = load_config("policy.yaml")
    assert cfg.approval_over < cfg.max_txn <= cfg.reserved
    assert cfg.currency == "INR" and 1 <= cfg.expires_days <= 90


def test_load_config_rejects_a_hold_that_could_never_fire(tmp_path):
    """The check has to be able to fail, so here is the input that fails it."""
    p = tmp_path / "bad.yaml"
    p.write_text(
        "block: {reserved: 1000000, currency: INR, expires_days: 30}\n"
        "rules: {max_txn: 200000, approval_over: 500000, velocity_calls: 10,\n"
        "        velocity_window_minutes: 1, reservation_ttl_minutes: 15,\n"
        "        derived_key_ttl_seconds: 300}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="approval_over"):
        load_config(str(p))


def test_a_block_longer_than_ninety_days_is_refused_at_load(tmp_path):
    """R2's ceiling. load_config enforces 1..90 days; nothing asserted it, so a
    policy.yaml saying 120 would have shipped a block outliving Reserve Pay's
    own maximum and R2 would still read as satisfied."""
    import yaml
    from src.policy import load_config

    def written(days):
        raw = {"block": {"reserved": 1000000, "currency": "INR", "expires_days": days},
               "rules": {"max_txn": 500000, "approval_over": 200000, "velocity_calls": 10,
                         "velocity_window_minutes": 1, "reservation_ttl_minutes": 15,
                         "derived_key_ttl_seconds": 300}}
        f = tmp_path / f"policy_{days}.yaml"
        f.write_text(yaml.safe_dump(raw), encoding="utf-8")
        return str(f)

    assert load_config(written(90)).expires_days == 90       # the boundary is inclusive
    for over in (91, 120):
        with pytest.raises(ValueError, match="1..90"):
            load_config(written(over))


def test_free_text_has_nowhere_to_reach_the_decision():
    """B15, proved structurally rather than by example.

    A product name, a note, an address or an upstream error string is exactly
    where a prompt injection rides. decide() cannot be steered by any of them
    because Call carries no field for them at all - the payload has nowhere to
    go. One assertion about the type is a stronger claim than any number of
    cases about values, and unlike a case list it cannot quietly rot.
    """
    fields = set(Call.__dataclass_fields__)
    assert fields == {"tool", "caller_id", "amount", "currency", "order_id", "payment_id",
                      "idem_key"}
    for free_text in ("notes", "receipt", "description", "customer", "address", "error"):
        assert free_text not in fields, f"{free_text} would give a payload a way in"


# Three claims the code makes by construction. Each was true and untested until
# 29 Aug 2026: a structural claim with no assertion behind it is one refactor
# away from being false quietly, and it goes quiet without a single test turning
# red. Same shape as the B15 test above - assert the shape, not a list of values.

def test_the_clock_belongs_to_the_server_alone():
    """B11. Expiry, the reservation TTL and the velocity window are all decided
    against `now`. If a caller could name it, R2 and R6 would both be advisory.

    decide() does take `now` as a parameter, which is deliberate: G5 wants a
    testable decision. What closes the hole is that the parameter is filled by
    ledger.authorize() from the server clock, and that no field a caller can
    populate carries a time at all.
    """
    import ast
    import inspect
    import pathlib

    from src import ledger

    timeish = {"now", "time", "timestamp", "date", "clock", "expires_at", "created_at"}
    assert not (set(Call.__dataclass_fields__) & timeish), "a caller could name the time"

    # The two money tools, read from source rather than through FastMCP's
    # wrapper, so this keeps working if the decorator's internals change.
    source = pathlib.Path(inspect.getfile(ledger)).with_name("server.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    tools = {n.name: n for n in ast.walk(tree)
             if isinstance(n, ast.AsyncFunctionDef) and n.name in ("create_order", "capture_payment")}
    assert set(tools) == {"create_order", "capture_payment"}, tools
    for name, fn in tools.items():
        taken = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
        assert not (taken & timeish), f"{name} accepts a caller-supplied time: {taken & timeish}"

    # And the default is the server clock, not something forwarded in.
    assert inspect.signature(ledger.authorize).parameters["now"].default is None
    assert "now = now or now_utc()" in inspect.getsource(ledger.authorize)


def test_the_upstream_url_can_only_come_from_config():
    """B23. A caller-supplied upstream would make the proxy an SSRF gadget that
    holds a live Razorpay credential and forwards it. The URL is a module
    constant, and there is no parameter through which a request could name
    another one.
    """
    import ast
    import inspect

    from src import upstream

    assert upstream.UPSTREAM_URL == "https://mcp.razorpay.com/mcp"
    assert list(inspect.signature(upstream.call_razorpay).parameters) == ["tool", "args"]

    opened = [n for n in ast.walk(ast.parse(inspect.getsource(upstream)))
              if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "streamablehttp_client"]
    assert len(opened) == 1, "one place opens the transport, or this check means nothing"
    target = opened[0].args[0]
    assert isinstance(target, ast.Name) and target.id == "UPSTREAM_URL", \
        "the transport is opened on something other than the pinned constant"


def test_duplicate_json_keys_cannot_smuggle_a_second_amount():
    """B29. `{"amount": 1, "amount": 999999}` is only a bypass if the proxy
    decides on one value and forwards bytes carrying both. Python keeps the last
    key, so one value survives parsing, and the forwarder takes a parsed dict
    rather than a body - so upstream cannot be shown a different number than the
    one the policy judged. That is G18, parse once and forward the object.
    """
    import inspect
    import json

    from src import upstream

    assert json.loads('{"amount": 1, "amount": 999999}') == {"amount": 999999}
    args = inspect.signature(upstream.call_razorpay).parameters["args"]
    assert args.annotation is dict, "the forwarder must take a parsed object, never raw bytes"
