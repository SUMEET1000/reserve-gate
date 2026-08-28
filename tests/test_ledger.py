"""The ledger under real SQLite, including the race the whole project exists to stop."""
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from src import ledger
from src.policy import ALLOW, BLOCK, HOLD, Call, Config

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
TOKEN = "a-test-bearer-token"
CALLER = ledger.caller_id_for(TOKEN)

CFG = Config(reserved=1000000, currency="INR", expires_days=30, max_txn=500000,
             approval_over=200000, velocity_calls=10, velocity_window_minutes=1,
             reservation_ttl_minutes=15, derived_key_ttl_seconds=300)

# Two calls of 500000 each fit R5 and each fit the block alone, but together they
# are 1100000 against 900000. That is the only shape where the race is visible:
# under CFG the per-call cap would refuse them first and prove nothing.
RACE_AMOUNT = 500000
RACE_CFG = Config(reserved=900000, currency="INR", expires_days=30, max_txn=600000,
                  approval_over=500000, velocity_calls=10, velocity_window_minutes=1,
                  reservation_ttl_minutes=15, derived_key_ttl_seconds=300)


@pytest.fixture
def db(tmp_path):
    """tmp_path because Windows refuses to delete a SQLite file that is still
    open, and WAL leaves -wal and -shm beside it (e11)."""
    return str(tmp_path / "ledger.db")


@pytest.fixture
def conn(db):
    c = ledger.connect(db)
    ledger.init(c, CFG, caller_id=CALLER, now=NOW)
    try:
        yield c
    finally:
        c.close()


def order(amount=50000, currency="INR", key=None) -> Call:
    return Call(tool="create_order", caller_id=CALLER, amount=amount,
                currency=currency, idem_key=key)


def balance(conn):
    b = ledger.snapshot(conn, CALLER)
    return b.spent, b.held, b.available


# the race -------------------------------------------------------------------

def _naive_debit(db, amount, results, i):
    """What a check-then-act handler looks like without the write lock. This is
    the control: if it did not overspend, the real test would prove nothing."""
    c = sqlite3.connect(db, timeout=5)
    c.isolation_level = None
    try:
        reserved, spent = c.execute("SELECT reserved, spent FROM blocks").fetchone()
        time.sleep(0.05)                      # the window a real handler spends upstream
        if spent + amount <= reserved:
            c.execute("UPDATE blocks SET spent = ?", (spent + amount,))
            results[i] = ALLOW
        else:
            results[i] = BLOCK
    finally:
        c.close()


def test_the_naive_version_really_does_overspend(db):
    """The control. Without it the test below could pass for any reason at all,
    including the race never happening."""
    conn = ledger.connect(db)
    ledger.init(conn, RACE_CFG, caller_id=CALLER, now=NOW)
    conn.close()

    results = [None, None]
    ts = [threading.Thread(target=_naive_debit, args=(db, RACE_AMOUNT, results, i))
          for i in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    # 1100000 authorised against a 900000 block, and the second write silently
    # overwrote the first, so the recorded total does not even show it.
    assert results == [ALLOW, ALLOW], results
    c = ledger.connect(db)
    assert c.execute("SELECT spent FROM blocks").fetchone()["spent"] == RACE_AMOUNT
    c.close()


def test_two_concurrent_calls_cannot_both_pass(db, monkeypatch):
    """B07, and the Day 2 gate. Two calls of 500000 against a 900000 block, each
    fitting alone. Exactly one may be authorised.

    decide() is slowed so both threads are genuinely inside authorize() at once.
    Without BEGIN IMMEDIATE around the read and the write, both would read the
    same empty block — which the control above demonstrates on this same data.
    """
    real = ledger.decide

    def slow(*a, **kw):
        time.sleep(0.05)
        return real(*a, **kw)

    monkeypatch.setattr(ledger, "decide", slow)

    setup = ledger.connect(db)
    ledger.init(setup, RACE_CFG, caller_id=CALLER, now=NOW)
    setup.close()

    results = [None, None]

    def attempt(i):
        c = ledger.connect(db)
        try:
            results[i], _ = ledger.authorize(c, order(RACE_AMOUNT, key=f"k{i}"),
                                             RACE_CFG, now=NOW)
        finally:
            c.close()

    ts = [threading.Thread(target=attempt, args=(i,)) for i in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    assert sorted(d.outcome for d in results) == [ALLOW, BLOCK], results

    # The rule matters as much as the outcome. Downgrading BEGIN IMMEDIATE to a
    # deferred BEGIN still yields one refusal, but it is G4 fail-closed on a lock
    # error rather than R1 reading the real balance. Asserting only the outcome
    # would pass either way and prove nothing about the locking.
    blocked = next(d for d in results if d.outcome == BLOCK)
    assert blocked.rule == "R1", f"refused for the wrong reason: {blocked}"

    c = ledger.connect(db)
    b = ledger.snapshot(c, CALLER)
    assert (b.held, b.spent) == (RACE_AMOUNT, 0)
    c.close()


def test_the_database_refuses_an_overspend_even_if_the_code_is_wrong(conn):
    """G17. The CHECK constraint is the backstop under the application logic, so
    a future bug in authorize() still cannot push the block negative."""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE blocks SET spent = 900000, held = 200000")


# the order-to-capture lifecycle ---------------------------------------------

def test_one_purchase_debits_the_block_exactly_once(conn):
    """E2. Reserve at create_order, commit at capture. Not both, not neither."""
    d, ref = ledger.authorize(conn, order(50000), CFG, now=NOW)
    assert d.outcome == ALLOW
    assert balance(conn) == (0, 50000, 950000)

    ledger.settle_order(conn, ref, order_id="order_X", result={"id": "order_X"})
    assert balance(conn) == (0, 50000, 950000)      # still only held

    cap = Call(tool="capture_payment", caller_id=CALLER, amount=50000,
               currency="INR", order_id="order_X")
    d2, ref2 = ledger.authorize(conn, cap, CFG, now=NOW)
    assert d2.outcome == ALLOW, d2
    ledger.settle_capture(conn, ref2, result={"id": "pay_X"})
    assert balance(conn) == (50000, 0, 950000)      # held became spent, once


def test_an_unpaid_order_returns_its_amount_to_the_block(conn):
    """E2's TTL. Ten abandoned orders must not consume a block that spent nothing."""
    d, ref = ledger.authorize(conn, order(50000), CFG, now=NOW)
    ledger.settle_order(conn, ref, order_id="order_Y", result={"id": "order_Y"})
    assert balance(conn)[1] == 50000

    later = NOW + timedelta(minutes=CFG.reservation_ttl_minutes, seconds=1)
    ledger.authorize(conn, order(100, key="probe"), CFG, now=later)
    assert balance(conn)[1] == 100                  # only the probe's own hold


def test_capture_after_the_ttl_is_refused(conn):
    d, ref = ledger.authorize(conn, order(50000), CFG, now=NOW)
    ledger.settle_order(conn, ref, order_id="order_Z", result={"id": "order_Z"})
    cap = Call(tool="capture_payment", caller_id=CALLER, amount=50000,
               currency="INR", order_id="order_Z")
    later = NOW + timedelta(minutes=CFG.reservation_ttl_minutes, seconds=1)
    d2, _ = ledger.authorize(conn, cap, CFG, now=later)
    assert d2.outcome == BLOCK and d2.rule == "R3", d2


def test_an_upstream_failure_gives_the_hold_back(conn):
    """B25. A 500 from Razorpay after the policy said ALLOW must not burn the block."""
    d, ref = ledger.authorize(conn, order(200000), CFG, now=NOW)
    assert balance(conn)[1] == 200000
    ledger.release(conn, ref, reason="upstream 500", now=NOW)
    assert balance(conn) == (0, 0, 1000000)


def test_a_released_call_can_be_retried_with_the_same_arguments(conn):
    """The dropped key is what makes the retry a fresh call and not a replay."""
    d, ref = ledger.authorize(conn, order(50000), CFG, now=NOW)
    ledger.release(conn, ref, reason="upstream timeout", now=NOW)
    d2, ref2 = ledger.authorize(conn, order(50000), CFG, now=NOW)
    assert d2.outcome == ALLOW and ref2 is not None, d2


def test_committing_the_same_payment_twice_is_a_noop(conn):
    """A webhook and the normal response may report the same capture."""
    d, ref = ledger.authorize(conn, order(50000), CFG, now=NOW)
    ledger.settle_order(conn, ref, order_id="order_D", result={"id": "order_D"})
    cap = Call(tool="capture_payment", caller_id=CALLER, amount=50000,
               currency="INR", order_id="order_D")
    _, ref2 = ledger.authorize(conn, cap, CFG, now=NOW)
    assert ledger.settle_capture(conn, ref2, result={"id": "pay_D"}) is True
    assert ledger.settle_capture(conn, ref2, result={"id": "pay_D"}) is False
    assert balance(conn) == (50000, 0, 950000)


# revocation, idempotency, velocity ------------------------------------------

def test_revocation_refuses_the_next_call_immediately(conn):
    """B12, R4. This is the video beat: revoke, then the next call refuses."""
    b = ledger.snapshot(conn, CALLER)
    assert ledger.authorize(conn, order(50000, key="before"), CFG, now=NOW)[0].outcome == ALLOW
    assert ledger.revoke(conn, b.block_id, now=NOW) is True
    d, _ = ledger.authorize(conn, order(50000, key="after"), CFG, now=NOW)
    assert d.outcome == BLOCK and d.rule == "R4", d


def test_revoking_twice_reports_that_nothing_changed(conn):
    b = ledger.snapshot(conn, CALLER)
    assert ledger.revoke(conn, b.block_id, now=NOW) is True
    assert ledger.revoke(conn, b.block_id, now=NOW) is False


def test_a_replayed_key_returns_the_first_result_and_holds_nothing_new(conn):
    """B08. One upstream result, one debit, however many times it is sent."""
    d, ref = ledger.authorize(conn, order(50000, key="idem-1"), CFG, now=NOW)
    ledger.settle_order(conn, ref, order_id="order_R", result={"id": "order_R"})
    held_before = balance(conn)[1]

    d2, ref2 = ledger.authorize(conn, order(50000, key="idem-1"), CFG, now=NOW)
    assert d2.outcome == ALLOW and d2.rule == "R7", d2
    assert balance(conn)[1] == held_before, "a replay must not reserve again"


def test_the_same_key_with_a_different_amount_is_a_conflict(conn):
    """B27, G16. Otherwise a key registered against 1 rupee answers for 10,000."""
    d, ref = ledger.authorize(conn, order(100, key="idem-2"), CFG, now=NOW)
    ledger.settle_order(conn, ref, order_id="order_S", result={"id": "order_S"})
    d2, _ = ledger.authorize(conn, order(1000000, key="idem-2"), CFG, now=NOW)
    assert d2.outcome == BLOCK and d2.rule == "G16", d2


def test_a_derived_key_stops_a_retry_but_expires(conn):
    """E13. The client sent no key, so the proxy derived one from the call. A
    retry seconds later collides; the same purchase an hour later does not."""
    d, ref = ledger.authorize(conn, order(50000), CFG, now=NOW)
    ledger.settle_order(conn, ref, order_id="order_T", result={"id": "order_T"})

    soon = NOW + timedelta(seconds=CFG.derived_key_ttl_seconds - 1)
    assert ledger.authorize(conn, order(50000), CFG, now=soon)[0].rule == "R7"

    later = NOW + timedelta(hours=1)
    d3, _ = ledger.authorize(conn, order(50000), CFG, now=later)
    assert d3.outcome == ALLOW and d3.rule == "", d3


def test_velocity_stops_a_runaway_loop(conn):
    """B24, OWASP LLM06:2026. The counter lives in the database and is read and
    written inside the same transaction as the decision."""
    outcomes = [ledger.authorize(conn, order(100, key=f"v{i}"), CFG, now=NOW)[0].outcome
                for i in range(CFG.velocity_calls + 3)]
    assert outcomes[:CFG.velocity_calls] == [ALLOW] * CFG.velocity_calls
    assert outcomes[CFG.velocity_calls:] == [BLOCK] * 3


def test_the_window_rolls(conn):
    for i in range(CFG.velocity_calls):
        ledger.authorize(conn, order(100, key=f"w{i}"), CFG, now=NOW)
    assert ledger.authorize(conn, order(100, key="over"), CFG, now=NOW)[0].rule == "R6"
    after = NOW + timedelta(minutes=CFG.velocity_window_minutes, seconds=1)
    assert ledger.authorize(conn, order(100, key="later"), CFG, now=after)[0].outcome == ALLOW


# block creation -------------------------------------------------------------

def test_a_hold_reserves_so_the_balance_cannot_be_spent_underneath_it(conn):
    """B33's other half. The amount is held while a human decides; releasing it
    when the hold is never approved is the same release() path as B25."""
    d, ref = ledger.authorize(conn, order(300000), CFG, now=NOW)
    assert d.outcome == HOLD, d
    assert balance(conn) == (0, 300000, 700000)
    ledger.release(conn, ref, reason="hold expired unapproved", now=NOW)
    assert balance(conn) == (0, 0, 1000000)


def test_an_expired_hold_cannot_be_approved(conn):
    decision, ref = ledger.authorize(conn, order(300000), CFG, now=NOW)
    assert decision.outcome == HOLD
    expired = NOW + timedelta(minutes=CFG.reservation_ttl_minutes, seconds=1)
    assert ledger.renew_hold(conn, ref, CFG.reservation_ttl_minutes, now=expired) is False
    assert balance(conn) == (0, 0, 1000000)


def test_block_ids_are_not_guessable(tmp_path):
    """B03. Two fresh ledgers must not produce related ids."""
    ids = []
    for i in range(2):
        c = ledger.connect(str(tmp_path / f"b{i}.db"))
        ids.append(ledger.init(c, CFG, caller_id=CALLER, now=NOW))
        c.close()
    assert ids[0] != ids[1]
    assert all(len(i) >= 20 and not i.isdigit() for i in ids)


def test_init_is_idempotent(conn):
    first = ledger.snapshot(conn, CALLER).block_id
    assert ledger.init(conn, CFG, caller_id=CALLER, now=NOW) == first


def test_an_empty_ledger_announces_itself(db, tmp_path, monkeypatch):
    """B32. A Render spin-down wipes the disk and the next boot rebuilds the
    block at full balance. That must be a record in the log, never silence."""
    log = tmp_path / "cold.jsonl"
    monkeypatch.setenv("RESERVE_GATE_AUDIT", str(log))
    c = ledger.connect(db)
    ledger.init(c, CFG, caller_id=CALLER, now=NOW)
    c.close()
    assert "COLD_START_LEDGER_RESET" in log.read_text(encoding="utf-8")


# two callers sharing one ledger -------------------------------------------

CALLER_B = ledger.caller_id_for("a-second-bearer-token")


@pytest.fixture
def two_callers(db):
    """The plan keeps caller-bound blocks precisely so this case exists: two
    agents, different budgets, one deployment."""
    c = ledger.connect(db)
    ledger.init(c, CFG, caller_id=CALLER, now=NOW)
    ledger.init(c, CFG, caller_id=CALLER_B, now=NOW)
    try:
        yield c
    finally:
        c.close()


def test_one_caller_cannot_capture_another_callers_order(two_callers):
    """B02, G2. A knows B's order id — Razorpay shows it to anyone who saw the
    order. Capturing it must not debit B's block."""
    conn = two_callers
    d, ref = ledger.authorize(conn, order(50000), CFG, now=NOW)
    assert d.outcome == ALLOW
    ledger.settle_order(conn, ref, order_id="order_MINE", result={"id": "order_MINE"})

    theft = Call(tool="capture_payment", caller_id=CALLER_B, amount=50000,
                 currency="INR", order_id="order_MINE")
    d2, ref2 = ledger.authorize(conn, theft, CFG, now=NOW)
    assert d2.outcome == BLOCK and d2.rule == "R3", d2
    assert ref2 is None

    a = ledger.snapshot(conn, CALLER)
    assert (a.spent, a.held) == (0, 50000), "the victim's block moved"


def test_two_callers_can_use_the_same_idempotency_key(two_callers):
    """A client picks its own key. If keys shared one namespace, the second
    caller to send 'order-1' would be handed the first caller's order."""
    conn = two_callers
    d, ref = ledger.authorize(conn, order(50000, key="order-1"), CFG, now=NOW)
    ledger.settle_order(conn, ref, order_id="order_A", result={"id": "order_A"})

    same_key = Call(tool="create_order", caller_id=CALLER_B, amount=50000,
                    currency="INR", idem_key="order-1")
    d2, ref2 = ledger.authorize(conn, same_key, CFG, now=NOW)
    assert d2.outcome == ALLOW and d2.rule == "", d2
    assert ref2 is not None and ref2.reservation_id != ref.reservation_id


def test_one_caller_cannot_reserve_a_key_to_break_another(two_callers):
    """Without a per-caller namespace, A registering 'x' against 100 makes every
    later call B sends under 'x' a G16 conflict — a denial of service on B."""
    conn = two_callers
    ledger.authorize(conn, order(100, key="x"), CFG, now=NOW)
    poisoned = Call(tool="create_order", caller_id=CALLER_B, amount=50000,
                    currency="INR", idem_key="x")
    assert ledger.authorize(conn, poisoned, CFG, now=NOW)[0].outcome == ALLOW


def test_an_identical_call_from_two_callers_derives_two_keys(two_callers):
    """R7 puts caller_id in the digest. Without it the second caller's unkeyed
    call replays the first caller's order instead of placing its own."""
    conn = two_callers
    d, ref = ledger.authorize(conn, order(50000), CFG, now=NOW)
    ledger.settle_order(conn, ref, order_id="order_A2", result={"id": "order_A2"})

    twin = Call(tool="create_order", caller_id=CALLER_B, amount=50000, currency="INR")
    assert ledger.args_hash(twin) != ledger.args_hash(order(50000))
    d2, _ = ledger.authorize(conn, twin, CFG, now=NOW)
    assert d2.outcome == ALLOW and d2.rule == "", d2


def test_each_caller_gets_its_own_block_and_its_own_balance(two_callers):
    conn = two_callers
    a, b = ledger.snapshot(conn, CALLER), ledger.snapshot(conn, CALLER_B)
    assert a.block_id != b.block_id
    ledger.authorize(conn, order(200000), CFG, now=NOW)
    assert ledger.snapshot(conn, CALLER).held == 200000
    assert ledger.snapshot(conn, CALLER_B).held == 0


def test_a_caller_id_never_contains_the_token():
    """G6. The token is hashed before it reaches the ledger or the audit log."""
    cid = ledger.caller_id_for(TOKEN)
    assert TOKEN not in cid and len(cid) == 16


def test_timestamps_are_utc_and_refuse_a_naive_datetime():
    """E12. A naive datetime here would stamp the local ledger IST and the
    deployed one UTC, and the audit trail would not reconcile."""
    assert ledger.iso(NOW).endswith("Z")
    with pytest.raises(AssertionError):
        ledger.iso(datetime(2026, 9, 1, 12, 0))


# found by an independent audit that never saw the build plan, 2026-08-28 -----

def test_a_capture_settling_after_revocation_is_recorded_as_late(conn, tmp_path, monkeypatch):
    """The hold was taken while the block was live and Razorpay has already
    captured, so the debit commits — refusing it would leave the ledger claiming
    a balance that is gone. What must not happen is that it commits unremarked.
    """
    log = tmp_path / "late.jsonl"
    monkeypatch.setenv("RESERVE_GATE_AUDIT", str(log))
    b = ledger.snapshot(conn, CALLER)
    _, ref = ledger.authorize(conn, order(50000), CFG, now=NOW)
    ledger.settle_order(conn, ref, order_id="order_L", result={"id": "order_L"})
    cap = Call(tool="capture_payment", caller_id=CALLER, amount=50000,
               currency="INR", order_id="order_L")
    _, ref2 = ledger.authorize(conn, cap, CFG, now=NOW)

    assert ledger.revoke(conn, b.block_id, now=NOW + timedelta(seconds=1)) is True
    ledger.settle_capture(conn, ref2, result={"id": "pay_L"}, now=NOW + timedelta(seconds=2))

    assert balance(conn) == (50000, 0, 950000)      # exposure unchanged by the commit
    assert "debit_committed_late" in log.read_text(encoding="utf-8")


def test_an_ordinary_capture_is_not_flagged_late(conn, tmp_path, monkeypatch):
    """The control for the test above: with the block live, the same sequence
    must produce a plain debit_committed and no late marker."""
    log = tmp_path / "ontime.jsonl"
    monkeypatch.setenv("RESERVE_GATE_AUDIT", str(log))
    _, ref = ledger.authorize(conn, order(50000), CFG, now=NOW)
    ledger.settle_order(conn, ref, order_id="order_M", result={"id": "order_M"})
    cap = Call(tool="capture_payment", caller_id=CALLER, amount=50000,
               currency="INR", order_id="order_M")
    _, ref2 = ledger.authorize(conn, cap, CFG, now=NOW)
    ledger.settle_capture(conn, ref2, result={"id": "pay_M"}, now=NOW)

    text = log.read_text(encoding="utf-8")
    assert "debit_committed" in text and "debit_committed_late" not in text


def test_an_unserialisable_amount_blocks_instead_of_raising(conn):
    """G4. args_hash json.dumps-es caller-supplied values, so it has to run
    inside the handler that turns an exception into a refusal, not before it."""
    d, ref = ledger.authorize(conn, order(object()), CFG, now=NOW)
    assert (d.outcome, d.rule, ref) == (BLOCK, "G4", None), d
    assert balance(conn) == (0, 0, 1000000)


def test_a_replay_returns_the_first_result(conn):
    """R7. Detecting the replay and dropping the stored reply leaves the retry
    indistinguishable from a fresh success, which is the duplicate R7 stops."""
    first = {"id": "order_R", "status": "created"}
    _, ref = ledger.authorize(conn, order(50000, key="stable"), CFG, now=NOW)
    ledger.settle_order(conn, ref, order_id="order_R", result=first)

    d, _ = ledger.authorize(conn, order(50000, key="stable"), CFG, now=NOW + timedelta(seconds=1))
    assert d.outcome == ALLOW and d.rule == "R7"
    assert d.detail["replay"] is True
    assert d.detail["result"] == first


def test_a_retry_that_only_changes_the_currency_case_is_still_a_retry(conn):
    """R0 treats "inr" and "INR" as one call, so the derived key has to as well.
    Hashing them apart makes a model's re-generated retry a second real order."""
    d1, _ = ledger.authorize(conn, order(50000, currency="INR"), CFG, now=NOW)
    d2, ref2 = ledger.authorize(conn, order(50000, currency="inr"), CFG,
                                now=NOW + timedelta(seconds=1))
    assert d1.outcome == ALLOW
    assert (d2.outcome, d2.rule) == (BLOCK, "R7"), d2   # in flight under the same key
    assert ref2 is None
    assert balance(conn) == (0, 50000, 950000)          # one hold, not two


def test_one_block_pays_for_many_purchases(conn):
    """R3, the NPCI semantic the whole project models: Single Block Multiple
    Debits. Every BLOCK path of R3 is asserted elsewhere; nothing asserted that
    the ordinary case — several debits against one live block — actually works."""
    for n in range(1, 4):
        oid = f"order_multi_{n}"
        d, ref = ledger.authorize(conn, order(200000, key=f"buy-{n}"), CFG, now=NOW)
        assert d.outcome == ALLOW, d
        ledger.settle_order(conn, ref, order_id=oid, result={"id": oid})
        cap = Call(tool="capture_payment", caller_id=CALLER, amount=200000,
                   currency="INR", order_id=oid)
        dc, refc = ledger.authorize(conn, cap, CFG, now=NOW)
        assert dc.outcome == ALLOW, dc
        ledger.settle_capture(conn, refc, result={"id": f"pay_{n}"}, now=NOW)
        assert balance(conn) == (200000 * n, 0, 1000000 - 200000 * n)

    # The block is not one-shot, and it still stops at its own edge.
    d, _ = ledger.authorize(conn, order(500000, key="over"), CFG, now=NOW)
    assert (d.outcome, d.rule) == (BLOCK, "R1"), d


def test_a_refused_runaway_loop_still_trips_the_speed_limit(conn):
    """B24. Unbounded consumption is about the calls made, not the calls that got
    through. Ten refusals in the window exhaust R6, so the eleventh is stopped by
    the throttle rather than being free forever."""
    over_the_cap = order(amount=CFG.max_txn + 1)
    rules = [ledger.authorize(conn, over_the_cap, CFG, now=NOW)[0].rule for _ in range(11)]
    assert rules[:10] == ["R5"] * 10, rules
    assert rules[10] == "R6", f"the eleventh refusal should be the throttle, got {rules[10]!r}"
