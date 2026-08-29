"""The block, its reservations and its counters, in SQLite.

policy.decide() is pure and knows nothing about storage. This module reads the
state, calls decide(), and writes the result — all inside one BEGIN IMMEDIATE
transaction, so a second caller cannot slip between the read and the write. That
race is the exact false-allow this project claims to prevent; see
tests/test_ledger.py::test_two_concurrent_calls_cannot_both_pass.

Money moves in two phases (G14). create_order holds the amount before the
upstream call and settle_order or release closes it out; capture_payment turns a
hold into spend only once Razorpay has confirmed. Nothing is ever debited before
the call it pays for, and only a failure whose outcome is known releases it.
"""
import hashlib
import json
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import audit
from .policy import ALLOW, BLOCK, HOLD, Block, Call, Config, Decision, Reservation, State, decide

SCHEMA = """
CREATE TABLE IF NOT EXISTS blocks (
  block_id   TEXT PRIMARY KEY,
  caller_id  TEXT NOT NULL UNIQUE,
  currency   TEXT NOT NULL,
  reserved   INTEGER NOT NULL,
  spent      INTEGER NOT NULL DEFAULT 0,
  held       INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT,
  frozen_at  TEXT,
  freeze_reason TEXT,
  CHECK (spent >= 0 AND held >= 0),
  -- The plan's G17 says CHECK (spent <= reserved). With two-phase debit that is
  -- not enough: it would let held grow past the block. The invariant the ledger
  -- actually has to hold is that committed and outstanding together fit.
  CHECK (spent + held <= reserved)
);

CREATE TABLE IF NOT EXISTS reservations (
  reservation_id TEXT PRIMARY KEY,
  block_id   TEXT NOT NULL REFERENCES blocks(block_id),
  order_id   TEXT UNIQUE,          -- Razorpay's id, NULL while the call is in flight
  amount     INTEGER NOT NULL,
  currency   TEXT NOT NULL,
  state      TEXT NOT NULL CHECK (state IN ('held', 'committed', 'released')),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  settled_at TEXT,
  payment_id TEXT,
  outcome_unknown INTEGER NOT NULL DEFAULT 0 CHECK (outcome_unknown IN (0, 1))
);

CREATE TABLE IF NOT EXISTS idempotency (
  caller_id  TEXT NOT NULL,
  key        TEXT NOT NULL,
  tool       TEXT NOT NULL,
  args_hash  TEXT NOT NULL,        -- G16: the key is bound to what it was used for
  result     TEXT,                 -- NULL while the first call is still running
  created_at TEXT NOT NULL,
  expires_at TEXT,             -- NULL for a client key; set for a derived one
  reservation_id TEXT,
  -- Composite, not `key` alone. A client picks its own key, so a bare primary
  -- key on it is one namespace shared by every caller: the second caller to use
  -- "order-1" would be handed the first caller's result, and a caller could
  -- reserve keys to make another caller's calls fail as G16 conflicts.
  PRIMARY KEY (caller_id, key)
);

CREATE TABLE IF NOT EXISTS money_calls (
  ts        TEXT NOT NULL,
  caller_id TEXT NOT NULL,
  tool      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS money_calls_window ON money_calls(caller_id, ts);

CREATE TABLE IF NOT EXISTS webhook_events (
  event_id   TEXT PRIMARY KEY,
  received_at TEXT NOT NULL,
  event_type TEXT NOT NULL,
  effect     TEXT NOT NULL,
  reason     TEXT NOT NULL
);
"""

MIGRATIONS = (
    ("frozen_at", "PRAGMA table_info(blocks)",
     "ALTER TABLE blocks ADD COLUMN frozen_at TEXT"),
    ("freeze_reason", "PRAGMA table_info(blocks)",
     "ALTER TABLE blocks ADD COLUMN freeze_reason TEXT"),
    ("payment_id", "PRAGMA table_info(reservations)",
     "ALTER TABLE reservations ADD COLUMN payment_id TEXT"),
    ("outcome_unknown", "PRAGMA table_info(reservations)",
     "ALTER TABLE reservations ADD COLUMN outcome_unknown INTEGER NOT NULL DEFAULT 0"),
    ("reservation_id", "PRAGMA table_info(idempotency)",
     "ALTER TABLE idempotency ADD COLUMN reservation_id TEXT"),
)


@dataclass(frozen=True)
class Ref:
    """What authorize() hands back so the call it approved can be closed out.

    Carries the idempotency key as well as the reservation, because a capture's
    key belongs to the capture and not to the order it commits — looking the row
    up by anything else would settle the wrong one.
    """
    reservation_id: str
    key: str
    caller_id: str


def iso(dt: datetime) -> str:
    """UTC, ISO-8601, Z-suffixed. A naive datetime is a bug, not an input: the
    local ledger would stamp IST and the deployed one UTC, and the audit trail
    would not reconcile with the Razorpay dashboard a judge is comparing it to."""
    assert dt.tzinfo is not None, "datetime must be timezone-aware"
    # A fixed width matters: these strings are compared with SQL `>` in the
    # velocity window, and "12:00:00.5Z" sorts before "12:00:00Z" because
    # "." < "Z". Always emitting microseconds keeps the ordering true.
    return dt.astimezone(timezone.utc).isoformat(
        timespec="microseconds").replace("+00:00", "Z")


def parse(s: str | None) -> datetime | None:
    return None if s is None else datetime.fromisoformat(s.replace("Z", "+00:00"))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def caller_id_for(token: str) -> str:
    """A stable id for the holder of a bearer token. The token itself never
    reaches the ledger, the audit log or an error string (G6)."""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def args_hash(call: Call, idempotency_args: dict | None = None) -> str:
    """What an idempotency key is bound to. Same key, different parameters is a
    conflict rather than a replay (G16), or an attacker registers a key against
    a 1-rupee call and the victim's real call returns its 'success'.

    The caller is part of the digest, as R7 specifies. Without
    `idempotency_args` this is the stable, money-only derived key: model-written
    notes and receipts may change when a retry is regenerated. Full upstream
    arguments are included only to bind a client-supplied key for G16.
    """
    # Currency is upper-cased to match R0, which treats "inr" and "INR" as one
    # call. Hashing them apart would derive two keys for a single purchase, and
    # a retry that varies the case becomes a second real order — the duplicate
    # the derived key exists to stop.
    values = {"caller_id": call.caller_id, "tool": call.tool,
              "amount": call.amount,
              "currency": call.currency.upper()
              if isinstance(call.currency, str) else call.currency,
              "order_id": call.order_id, "payment_id": call.payment_id}
    if idempotency_args is not None:
        upstream = dict(idempotency_args)
        if isinstance(upstream.get("currency"), str):
            upstream["currency"] = upstream["currency"].upper()
        values["upstream_args"] = upstream
    canonical = json.dumps(values, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def path() -> str:
    return os.environ.get("RESERVE_GATE_DB", "reserve_gate.db")


def connect(db: str | None = None) -> sqlite3.Connection:
    """WAL so a reader never blocks the writer, busy_timeout so contention waits
    instead of raising `database is locked` and failing an honest call closed.

    isolation_level = None hands transaction control to this module. The modern
    3.12+ `autocommit=False` is not used: it raises "cannot start a transaction
    within a transaction" on BEGIN IMMEDIATE here (Python 3.13.5, SQLite 3.50.2).
    """
    conn = sqlite3.connect(db or path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    for column, check, statement in MIGRATIONS:
        if column not in {row["name"] for row in conn.execute(check)}:
            conn.execute(statement)
    return conn


def init(conn: sqlite3.Connection, config: Config, *, caller_id: str,
         now: datetime | None = None) -> str:
    """Return this caller's block id, creating the block if there is none.

    Render's free tier has no disk, so a redeploy or a fifteen-minute spin-down
    erases this database and the next boot rebuilds the block at full balance.
    That is a real bypass of R1 on the deployed URL and it is announced rather
    than hidden: the reset is an audit record, and every number in the eval
    report comes from the local run where the ledger survives.
    """
    now = now or now_utc()
    row = conn.execute("SELECT block_id FROM blocks WHERE caller_id = ?", (caller_id,)).fetchone()
    if row:
        return row["block_id"]

    block_id = secrets.token_urlsafe(16)      # B03: never sequential, never guessable
    expires_at = now + timedelta(days=config.expires_days)
    conn.execute(
        "INSERT INTO blocks (block_id, caller_id, currency, reserved, created_at, expires_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (block_id, caller_id, config.currency, config.reserved, iso(now), iso(expires_at)))
    audit.record(event="COLD_START_LEDGER_RESET", block_id=block_id, caller_id=caller_id,
                 reserved=config.reserved, currency=config.currency,
                 expires_at=iso(expires_at),
                 note="ledger had no block for this caller; a full balance was created")
    return block_id


def snapshot(conn: sqlite3.Connection, caller_id: str) -> Block | None:
    row = conn.execute("SELECT * FROM blocks WHERE caller_id = ?", (caller_id,)).fetchone()
    if not row:
        return None
    return Block(block_id=row["block_id"], caller_id=row["caller_id"], currency=row["currency"],
                 reserved=row["reserved"], spent=row["spent"], held=row["held"],
                 expires_at=parse(row["expires_at"]), revoked_at=parse(row["revoked_at"]),
                 frozen_at=parse(row["frozen_at"]), freeze_reason=row["freeze_reason"])


def _expire_stale(conn: sqlite3.Connection, now: datetime) -> None:
    """Housekeeping that has to run before the balance is read.

    E2: an order that was never paid returns its amount to the block, so ten
    abandoned orders do not consume a block that spent nothing.

    R7: a derived key that is past its five minutes is deleted rather than left
    to sit. Ignoring it on lookup is not enough — the row still holds the primary
    key, so the retry that should now be a fresh call collides on INSERT instead.
    """
    for r in conn.execute("SELECT reservation_id, block_id, amount FROM reservations"
                          " WHERE state = 'held' AND outcome_unknown = 0 AND expires_at <= ?",
                          (iso(now),)).fetchall():
        conn.execute("UPDATE reservations SET state = 'released', settled_at = ?"
                     " WHERE reservation_id = ?", (iso(now), r["reservation_id"]))
        conn.execute("UPDATE blocks SET held = held - ? WHERE block_id = ?",
                     (r["amount"], r["block_id"]))
        audit.record(event="reservation_expired", reservation_id=r["reservation_id"],
                     amount=r["amount"], block_id=r["block_id"])
    conn.execute("DELETE FROM idempotency WHERE expires_at IS NOT NULL AND expires_at <= ?",
                 (iso(now),))


def _load_state(conn: sqlite3.Connection, call: Call, key: str, now: datetime,
                velocity: int, bound_hash: str) -> State:
    block = snapshot(conn, call.caller_id)
    row = conn.execute(
        "SELECT args_hash, result FROM idempotency"
        " WHERE caller_id = ? AND key = ? AND (expires_at IS NULL OR expires_at > ?)",
        (call.caller_id, key, iso(now))).fetchone()
    conflict = bool(row) and row["args_hash"] != bound_hash
    replay = json.loads(row["result"]) if row and not conflict and row["result"] else None
    in_flight = bool(row) and not conflict and row["result"] is None

    reservation = None
    if call.order_id:
        # Joined to blocks on caller_id, not looked up by order_id alone. An
        # order id is Razorpay's handle and anyone who saw the order holds it;
        # without the join another caller could capture this order and the debit
        # would land on the block that reserved for it, not on theirs.
        r = conn.execute(
            "SELECT r.* FROM reservations r JOIN blocks b ON b.block_id = r.block_id"
            " WHERE r.order_id = ? AND b.caller_id = ?",
            (call.order_id, call.caller_id)).fetchone()
        if r:
            reservation = Reservation(reservation_id=r["reservation_id"], block_id=r["block_id"],
                                      amount=r["amount"], currency=r["currency"],
                                      state=r["state"], expires_at=parse(r["expires_at"]),
                                      order_id=r["order_id"], payment_id=r["payment_id"])
    return State(block=block, velocity_count=velocity, replay=replay,
                 in_flight=in_flight, conflict=conflict, reservation=reservation)


def authorize(conn: sqlite3.Connection, call: Call, config: Config, *,
              now: datetime | None = None,
              receipt: str | None = None,
              idempotency_args: dict | None = None) -> tuple[Decision, Ref | None]:
    """Take the decision and record it, atomically.

    Returns the decision and, when money may move, the reservation id the caller
    must later settle or release. Everything from the balance read to the write
    happens under one write lock: two concurrent calls that each fit the block
    but together exceed it must not both see the same balance (B07).

    `receipt` is the caller's own label for the purchase and is written to the
    audit record so the trail names what was bought rather than an amount on its
    own. It is deliberately a parameter here and not a field on `Call`: the
    decision function must stay unable to read caller-supplied free text, which
    is the structural half of B15. `idempotency_args` binds that free text to a
    client-supplied key without exposing it to the decision; derived keys ignore
    it so a reworded retry still collides.
    """
    now = now or now_utc()

    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as e:
        # B31. Contention must fail closed, never fall through to an allow.
        audit.record(event="block", rule="G4", reason=f"could not take the write lock: {e}")
        return Decision(BLOCK, "G4", f"ledger busy: {e}"), None

    try:
        # Inside the transaction, because args_hash json.dumps-es caller-supplied
        # values: an argument it cannot serialise has to fail closed through the
        # G4 handler below rather than raise past it.
        key = call.idem_key or args_hash(call)
        bound_hash = (args_hash(call, idempotency_args)
                      if call.idem_key else args_hash(call))
        derived = call.idem_key is None
        _expire_stale(conn, now)
        window = iso(now - timedelta(minutes=config.velocity_window_minutes))
        # B13. The velocity check and the row that increments it share this
        # transaction; two statements outside one is the bypass.
        count = conn.execute("SELECT COUNT(*) c FROM money_calls WHERE caller_id = ? AND ts > ?",
                             (call.caller_id, window)).fetchone()["c"]
        state = _load_state(conn, call, key, now, count, bound_hash)

        d = decide(call, state, config, now)
        ref = None
        reservation_id = None

        # R6 counts every call that is not a replay, refusals included. A
        # runaway loop making a thousand junk calls is unbounded consumption
        # whether or not the gate lets any of them through (OWASP LLM06:2026);
        # counting only the allowed ones leaves that loop unbounded. The cost is
        # that an agent which keeps guessing wrong burns its own minute of
        # quota, and it is the right party to charge for it.
        if state.replay is None:
            conn.execute("INSERT INTO money_calls (ts, caller_id, tool) VALUES (?, ?, ?)",
                         (iso(now), call.caller_id, call.tool))
        if d.outcome in (ALLOW, HOLD) and state.replay is None:
            reservation_id = (secrets.token_urlsafe(16) if call.tool == "create_order"
                              else state.reservation.reservation_id)
            conn.execute(
                "INSERT INTO idempotency (key, caller_id, tool, args_hash, created_at, expires_at,"
                " reservation_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (key, call.caller_id, call.tool, bound_hash, iso(now),
                 iso(now + timedelta(seconds=config.derived_key_ttl_seconds)) if derived else None,
                 reservation_id))
            if call.tool == "create_order":
                conn.execute(
                    "INSERT INTO reservations (reservation_id, block_id, amount, currency,"
                    " state, created_at, expires_at) VALUES (?, ?, ?, ?, 'held', ?, ?)",
                    (reservation_id, state.block.block_id, call.amount, call.currency, iso(now),
                     iso(now + timedelta(minutes=config.reservation_ttl_minutes))))
                # A HOLD reserves too, so an approval cannot be granted against a
                # balance something else spent in the meantime. B33 returns it if
                # the hold is never approved.
                conn.execute("UPDATE blocks SET held = held + ? WHERE block_id = ?",
                             (call.amount, state.block.block_id))
            elif call.payment_id:
                conn.execute("UPDATE reservations SET payment_id = ? WHERE reservation_id = ?"
                             " AND (payment_id IS NULL OR payment_id = ?)",
                             (call.payment_id, reservation_id, call.payment_id))
            ref = Ref(reservation_id=reservation_id, key=key, caller_id=call.caller_id)
        conn.execute("COMMIT")
    except Exception as e:
        conn.execute("ROLLBACK")
        # G4 again, at the storage layer: a constraint violation, an OverflowError
        # from an absurd amount, anything at all, is a refusal.
        audit.record(event="block", rule="G4", tool=call.tool,
                     reason=f"{type(e).__name__}: {e}")
        return Decision(BLOCK, "G4", f"ledger refused the write: {type(e).__name__}: {e}"), None

    audit.record(event=d.outcome.lower(), kind="money", tool=call.tool, rule=d.rule,
                 reason=d.reason, caller_id=call.caller_id, amount=call.amount,
                 currency=call.currency, receipt=receipt, idem_key_derived=derived,
                 block_id=state.block.block_id if state.block else None,
                 reservation_id=reservation_id, detail=d.detail)
    return d, ref


def settle_order(conn: sqlite3.Connection, ref: Ref, *, order_id: str, result: dict) -> None:
    """Upstream created the order. Bind Razorpay's id to the hold already taken,
    and store the reply so a replay of this key returns it (R7)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("UPDATE reservations SET order_id = ? WHERE reservation_id = ?"
                     " AND state = 'held'", (order_id, ref.reservation_id))
        conn.execute("UPDATE idempotency SET result = ? WHERE caller_id = ? AND key = ?",
                     (json.dumps(result), ref.caller_id, ref.key))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    audit.record(event="reservation_bound", reservation_id=ref.reservation_id, order_id=order_id)


def release(conn: sqlite3.Connection, ref: Ref, *, reason: str,
            now: datetime | None = None) -> None:
    """B25. Razorpay proved the call failed after policy said ALLOW. The hold
    goes back to the block; a known failed call must never burn the balance."""
    now = now or now_utc()
    conn.execute("BEGIN IMMEDIATE")
    try:
        r = conn.execute("SELECT block_id, amount, state FROM reservations"
                         " WHERE reservation_id = ?", (ref.reservation_id,)).fetchone()
        if r and r["state"] == "held":
            conn.execute("UPDATE reservations SET state = 'released', settled_at = ?"
                         " WHERE reservation_id = ?", (iso(now), ref.reservation_id))
            conn.execute("UPDATE blocks SET held = held - ? WHERE block_id = ?",
                         (r["amount"], r["block_id"]))
        # The key goes with it, so the client's retry is a fresh call rather than
        # a permanent replay of a failure.
        conn.execute("DELETE FROM idempotency WHERE caller_id = ? AND key = ? AND result IS NULL",
                     (ref.caller_id, ref.key))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    audit.record(event="reservation_released", reservation_id=ref.reservation_id, reason=reason)


def mark_capture_pending(conn: sqlite3.Connection, ref: Ref) -> None:
    """Keep this hold until a verified capture outcome reconciles it."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute("UPDATE reservations SET outcome_unknown = 1"
                           " WHERE reservation_id = ? AND state = 'held'",
                           (ref.reservation_id,))
        if cur.rowcount != 1:
            raise RuntimeError(f"reservation {ref.reservation_id} is not held")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def renew_hold(conn: sqlite3.Connection, ref: Ref, ttl_minutes: int, *,
               now: datetime | None = None) -> bool:
    """Claim an unexpired HOLD for one approval attempt and extend its lease."""
    now = now or now_utc()
    conn.execute("BEGIN IMMEDIATE")
    try:
        _expire_stale(conn, now)
        # R4/R2: an approval is a fresh money call, so the block has to be live
        # now, not merely at the moment the hold was taken. Reading the
        # reservation alone let a revoked or expired block still be spent.
        r = conn.execute("SELECT r.state, r.expires_at, b.revoked_at, b.frozen_at,"
                         " b.expires_at AS block_expires_at FROM reservations r"
                         " JOIN blocks b ON b.block_id = r.block_id"
                         " WHERE r.reservation_id = ?", (ref.reservation_id,)).fetchone()
        open_ = bool(r and r["state"] == "held" and parse(r["expires_at"]) > now
                     and r["revoked_at"] is None and r["frozen_at"] is None
                     and now < parse(r["block_expires_at"]))
        if open_:
            conn.execute("UPDATE reservations SET expires_at = ? WHERE reservation_id = ?",
                         (iso(now + timedelta(minutes=ttl_minutes)), ref.reservation_id))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    audit.record(event="hold_claimed" if open_ else "hold_expired",
                 reservation_id=ref.reservation_id)
    return open_


def settle_capture(conn: sqlite3.Connection, ref: Ref, *, result: dict,
                   now: datetime | None = None) -> str:
    """Razorpay captured the payment. The hold becomes spend — the one and only
    place the block is actually debited.

    B26: this raises rather than swallowing. A capture that happened upstream and
    was not recorded here has to be visible, not silently forgotten.

    Returns "committed", "duplicate" (R7 replay, the first result stands) or
    "refused". Only the first two mean the caller may hand the reply back; a bool
    made a frozen conflict indistinguishable from a replay.
    """
    now = now or now_utc()
    conn.execute("BEGIN IMMEDIATE")
    try:
        r = conn.execute("SELECT block_id, amount, state, payment_id FROM reservations"
                         " WHERE reservation_id = ?", (ref.reservation_id,)).fetchone()
        payment_id = result.get("id")
        if not isinstance(payment_id, str) or not payment_id.strip():
            raise RuntimeError("captured result has no payment id")
        status = result.get("status")
        if status is not None and status != "captured":
            conn.execute("UPDATE reservations SET outcome_unknown = 0"
                         " WHERE reservation_id = ?", (ref.reservation_id,))
            conn.execute("COMMIT")
            audit.record(event="capture_not_captured", reservation_id=ref.reservation_id,
                         payment_id=payment_id, status=str(status))
            return "refused"
        other = conn.execute(
            "SELECT reservation_id FROM reservations WHERE payment_id = ?"
            " AND state = 'committed' AND reservation_id <> ?",
            (payment_id, ref.reservation_id)).fetchone()
        if r and other:
            conn.execute("UPDATE blocks SET frozen_at = ?, freeze_reason = ? WHERE block_id = ?",
                         (iso(now), "payment committed to another reservation", r["block_id"]))
            conn.execute("COMMIT")
            audit.record(event="block_frozen", reservation_id=ref.reservation_id,
                         block_id=r["block_id"], reason="payment committed elsewhere")
            return "refused"
        if r and r["state"] == "committed" and r["payment_id"] == payment_id:
            conn.execute("UPDATE idempotency SET result = ? WHERE caller_id = ? AND key = ?",
                         (json.dumps(result), ref.caller_id, ref.key))
            conn.execute("COMMIT")
            audit.record(event="capture_duplicate", reservation_id=ref.reservation_id,
                         payment_id=payment_id, source="normal_response")
            return "duplicate"
        if r and r["state"] == "committed":
            conn.execute("UPDATE blocks SET frozen_at = ?, freeze_reason = ? WHERE block_id = ?",
                         (iso(now), "different payment committed for one reservation", r["block_id"]))
            conn.execute("COMMIT")
            audit.record(event="block_frozen", reservation_id=ref.reservation_id,
                         block_id=r["block_id"], reason="different payment committed")
            return "refused"
        if not r or r["state"] != "held":
            raise RuntimeError(f"reservation {ref.reservation_id} is"
                               f" {r['state'] if r else 'missing'}, not held;"
                               " refusing to commit it twice")
        # The hold was taken while the block was live and Razorpay has already
        # captured, so refusing the debit here would leave the ledger claiming a
        # balance that no longer exists. It commits, and the boundary it crossed
        # is named in the audit record instead of passing unremarked.
        b = conn.execute("SELECT expires_at, revoked_at FROM blocks WHERE block_id = ?",
                         (r["block_id"],)).fetchone()
        if b and b["revoked_at"] is not None:
            late = f"revoked_at={b['revoked_at']}"
        elif b and now >= parse(b["expires_at"]):
            late = f"expires_at={b['expires_at']}"
        else:
            late = None
        if r["payment_id"] is not None and r["payment_id"] != payment_id:
            conn.execute("UPDATE blocks SET frozen_at = ?, freeze_reason = ? WHERE block_id = ?",
                         (iso(now), "capture response payment mismatch", r["block_id"]))
            conn.execute("COMMIT")
            audit.record(event="block_frozen", reservation_id=ref.reservation_id,
                         block_id=r["block_id"], reason="capture response payment mismatch")
            return "refused"
        conn.execute("UPDATE reservations SET state = 'committed', settled_at = ?, payment_id = ?"
                     " WHERE reservation_id = ?", (iso(now), payment_id, ref.reservation_id))
        conn.execute("UPDATE blocks SET held = held - ?, spent = spent + ? WHERE block_id = ?",
                     (r["amount"], r["amount"], r["block_id"]))
        conn.execute("UPDATE idempotency SET result = ? WHERE caller_id = ? AND key = ?",
                     (json.dumps(result), ref.caller_id, ref.key))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    audit.record(event="debit_committed" if late is None else "debit_committed_late",
                 reservation_id=ref.reservation_id, amount=r["amount"],
                  block_id=r["block_id"], upstream_id=result.get("id"), settled_after=late)
    return "committed"


def reconcile_webhook(conn: sqlite3.Connection, event_id: str, event_type: str,
                      payment: dict | None, *, now: datetime | None = None) -> dict:
    """Atomically deduplicate and reconcile one authenticated Razorpay event."""
    now = now or now_utc()
    conn.execute("BEGIN IMMEDIATE")
    block_id = reservation_id = None
    frozen = False
    try:
        try:
            conn.execute("INSERT INTO webhook_events"
                         " (event_id, received_at, event_type, effect, reason)"
                         " VALUES (?, ?, ?, 'NOOP', 'processing')",
                         (event_id, iso(now), event_type))
        except sqlite3.IntegrityError:
            if conn.execute("SELECT 1 FROM webhook_events WHERE event_id = ?",
                            (event_id,)).fetchone() is None:
                raise
            conn.execute("COMMIT")
            result = {"accepted": True, "applied": False, "reason": "duplicate_event",
                      "effect": "NOOP"}
            audit.record(event="webhook_duplicate", event_id=event_id, event_type=event_type)
            return result

        effect, reason = "NOOP", "unsupported_event"
        if event_type == "payment.captured":
            entity = payment if isinstance(payment, dict) else {}
            payment_id, order_id = entity.get("id"), entity.get("order_id")
            if not isinstance(order_id, str) or not order_id.strip():
                effect, reason = "REJECT", "invalid_payment"
            else:
                r = conn.execute(
                    "SELECT r.*, b.frozen_at FROM reservations r"
                    " JOIN blocks b ON b.block_id = r.block_id WHERE r.order_id = ?",
                    (order_id,)).fetchone()
                if r is None:
                    reason = "unknown_order"
                else:
                    block_id, reservation_id = r["block_id"], r["reservation_id"]
                    other = (conn.execute(
                        "SELECT 1 FROM reservations WHERE payment_id = ?"
                        " AND state = 'committed' AND reservation_id <> ?",
                        (payment_id, r["reservation_id"])).fetchone()
                        if isinstance(payment_id, str) and payment_id.strip() else None)
                    mismatch = (not isinstance(payment_id, str) or not payment_id.strip()
                                or (r["payment_id"] is not None and r["payment_id"] != payment_id)
                                or type(entity.get("amount")) is not int
                                or entity.get("amount") != r["amount"]
                                or not isinstance(entity.get("currency"), str)
                                or entity["currency"].upper() != r["currency"].upper()
                                or entity.get("status") != "captured")
                    conflict = r["state"] == "committed" and r["payment_id"] != payment_id
                    late = r["state"] == "released"
                    if mismatch or conflict or late or other:
                        reason = ("payment_mismatch" if mismatch else
                                  "payment_already_committed_elsewhere" if other else
                                  "different_payment" if conflict else "late_capture")
                        conn.execute("UPDATE blocks SET frozen_at = ?, freeze_reason = ?"
                                     " WHERE block_id = ? AND frozen_at IS NULL",
                                     (iso(now), reason, r["block_id"]))
                        effect, frozen = "REJECT", True
                    elif r["state"] == "committed":
                        reason = "already_committed"
                    elif r["frozen_at"] is not None:
                        effect, reason = "REJECT", "block_frozen"
                    else:
                        result = {"id": payment_id, "order_id": order_id,
                                  "amount": entity["amount"], "currency": entity["currency"],
                                  "status": entity["status"]}
                        conn.execute("UPDATE reservations SET state = 'committed', settled_at = ?,"
                                     " payment_id = ? WHERE reservation_id = ? AND state = 'held'",
                                     (iso(now), payment_id, r["reservation_id"]))
                        conn.execute("UPDATE blocks SET held = held - ?, spent = spent + ?"
                                     " WHERE block_id = ?", (r["amount"], r["amount"], r["block_id"]))
                        conn.execute("UPDATE idempotency SET result = ? WHERE reservation_id = ?"
                                     " AND tool = 'capture_payment' AND result IS NULL",
                                     (json.dumps(result), r["reservation_id"]))
                        effect, reason = "APPLY", "capture_applied"
        conn.execute("UPDATE webhook_events SET effect = ?, reason = ? WHERE event_id = ?",
                     (effect, reason, event_id))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    audit.record(event="webhook_" + effect.lower(), event_id=event_id,
                 event_type=event_type, reason=reason, block_id=block_id,
                 reservation_id=reservation_id, frozen=frozen)
    return {"accepted": True, "applied": effect == "APPLY", "reason": reason,
            "effect": effect}


def revoke(conn: sqlite3.Connection, block_id: str, *, now: datetime | None = None) -> bool:
    """R4. Every later money call refuses. A reservation authorised before this
    instant still settles, because Razorpay has already taken the money and the
    hold was counted against the block when it was taken — the balance does not
    move. settle_capture records that one as debit_committed_late."""
    now = now or now_utc()
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute("UPDATE blocks SET revoked_at = ? WHERE block_id = ?"
                           " AND revoked_at IS NULL", (iso(now), block_id))
        changed = cur.rowcount > 0
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    audit.record(event="block_revoked", block_id=block_id, changed=changed)
    return changed


def unfreeze(conn: sqlite3.Connection, block_id: str) -> bool | None:
    """Clear only reconciliation freeze state. Unknown blocks return None."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        if conn.execute("SELECT 1 FROM blocks WHERE block_id = ?", (block_id,)).fetchone() is None:
            conn.execute("COMMIT")
            return None
        cur = conn.execute("UPDATE blocks SET frozen_at = NULL, freeze_reason = NULL"
                           " WHERE block_id = ? AND frozen_at IS NOT NULL", (block_id,))
        changed = cur.rowcount > 0
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    audit.record(event="block_unfrozen", block_id=block_id, changed=changed)
    return changed
