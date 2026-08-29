import hashlib
import hmac
import json
import sqlite3

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from src import ledger, server
from src.policy import Call

SECRET = "test-webhook-secret-not-real"
CALLER = ledger.caller_id_for("webhook-test-caller")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("RESERVE_GATE_DB", str(tmp_path / "ledger.db"))
    app = Starlette(routes=[Route("/webhook", server.webhook, methods=["POST"])])
    with TestClient(server.bearer_auth(app)) as value:
        yield value


def signed(client, payload, event_id="evt_1", *, body=None, secret=SECRET):
    body = body if body is not None else json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {"X-Razorpay-Signature": signature}
    if event_id is not None:
        headers["x-razorpay-event-id"] = event_id
    return client.post("/webhook", content=body, headers=headers)


def payment_event(order_id="order_1", payment_id="pay_1", amount=50000,
                  currency="INR", status="captured"):
    return {"event": "payment.captured", "payload": {"payment": {"entity": {
        "id": payment_id, "order_id": order_id, "amount": amount,
        "currency": currency, "status": status,
    }}}}


def held_order(order_id="order_1", amount=50000):
    conn = ledger.connect()
    ledger.init(conn, server.config(), caller_id=CALLER)
    call = Call("create_order", CALLER, amount, "INR", idem_key="create-" + order_id)
    _, ref = ledger.authorize(conn, call, server.config())
    ledger.settle_order(conn, ref, order_id=order_id, result={"id": order_id})
    return conn, ref


def capture_ref(conn, order_id="order_1", payment_id="pay_1", amount=50000, key="cap-1"):
    call = Call("capture_payment", CALLER, amount, "INR", order_id, payment_id, key)
    decision, ref = ledger.authorize(conn, call, server.config())
    assert decision.allowed
    return call, ref


def test_missing_signature_is_401_without_parsing(client):
    assert client.post("/webhook", content=b"not json").status_code == 401


def test_wrong_signature_is_401(client):
    assert signed(client, payment_event(), secret="wrong").status_code == 401


def test_signature_over_reserialized_json_is_401(client):
    payload = payment_event()
    compact = json.dumps(payload, separators=(",", ":")).encode()
    pretty = json.dumps(payload, indent=2).encode()
    signature = hmac.new(SECRET.encode(), compact, hashlib.sha256).hexdigest()
    r = client.post("/webhook", content=pretty,
                    headers={"X-Razorpay-Signature": signature,
                             "x-razorpay-event-id": "evt_reserialized"})
    assert r.status_code == 401


def test_missing_event_id_is_400(client):
    assert signed(client, payment_event(), event_id=None).status_code == 400


def test_malformed_signed_json_is_400(client):
    assert signed(client, {}, body=b"{").status_code == 400


def test_missing_server_secret_is_503(client, monkeypatch):
    monkeypatch.delenv("RAZORPAY_WEBHOOK_SECRET")
    assert client.post("/webhook").status_code == 503


def test_oversized_body_is_rejected_before_parsing(client):
    body = b"x" * 1_000_001
    signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    r = client.post("/webhook", content=body,
                    headers={"X-Razorpay-Signature": signature,
                             "x-razorpay-event-id": "evt_large"})
    assert r.status_code == 413


def test_unsupported_signed_event_is_a_noop(client):
    r = signed(client, {"event": "payment.authorized"})
    assert r.status_code == 200 and r.json() == {
        "accepted": True, "applied": False, "reason": "unsupported_event"}


def test_unknown_order_is_a_noop(client):
    r = signed(client, payment_event(order_id="order_unknown"))
    assert r.status_code == 200 and r.json()["reason"] == "unknown_order"


def test_first_capture_moves_held_to_spent_once(client):
    conn, _ = held_order()
    r = signed(client, payment_event())
    block = ledger.snapshot(conn, CALLER)
    assert r.json()["applied"] is True
    assert (block.spent, block.held) == (50000, 0)
    conn.close()


def test_repeated_event_id_is_a_noop(client):
    conn, _ = held_order()
    assert signed(client, payment_event(), "evt_duplicate").json()["applied"] is True
    for _ in range(2):
        r = signed(client, payment_event(), "evt_duplicate")
        assert r.json()["reason"] == "duplicate_event"
    block = ledger.snapshot(conn, CALLER)
    assert (block.spent, block.held) == (50000, 0)
    conn.close()


@pytest.mark.parametrize("field,value", [
    ("amount", 50001),
    ("currency", "USD"),
    ("status", "authorized"),
    ("id", "pay_other"),
])
def test_mismatched_capture_freezes_the_block(client, field, value):
    conn, _ = held_order()
    capture_ref(conn)
    payload = payment_event()
    payload["payload"]["payment"]["entity"][field] = value
    r = signed(client, payload, "evt_mismatch_" + field)
    block = ledger.snapshot(conn, CALLER)
    assert r.json() == {"accepted": True, "applied": False, "reason": "payment_mismatch"}
    assert block.frozen_at is not None
    decision, _ = ledger.authorize(
        conn, Call("create_order", CALLER, 100, "INR", idem_key="after-" + field),
        server.config())
    assert (decision.outcome, decision.rule) == ("BLOCK", "G4")
    conn.close()


def test_capture_after_release_freezes_the_block(client):
    conn, ref = held_order()
    ledger.release(conn, ref, reason="expired")
    r = signed(client, payment_event(), "evt_late")
    assert r.json()["reason"] == "late_capture"
    assert ledger.snapshot(conn, CALLER).frozen_at is not None
    conn.close()


def test_normal_response_wins_and_webhook_is_same_payment_noop(client):
    conn, _ = held_order()
    _, ref = capture_ref(conn)
    assert ledger.settle_capture(conn, ref, result={"id": "pay_1"}) == "committed"
    r = signed(client, payment_event(), "evt_after_normal")
    assert r.json()["reason"] == "already_committed"
    assert (ledger.snapshot(conn, CALLER).spent, ledger.snapshot(conn, CALLER).held) == (50000, 0)
    conn.close()


def test_webhook_wins_and_normal_response_is_same_payment_noop(client):
    conn, _ = held_order()
    call, ref = capture_ref(conn, key="capture-race")
    assert signed(client, payment_event(), "evt_before_normal").json()["applied"] is True
    assert ledger.settle_capture(conn, ref, result={"id": "pay_1"}) == "duplicate"
    replay, _ = ledger.authorize(conn, call, server.config())
    assert replay.rule == "R7" and replay.detail["result"]["id"] == "pay_1"
    assert (ledger.snapshot(conn, CALLER).spent, ledger.snapshot(conn, CALLER).held) == (50000, 0)
    conn.close()


def test_same_payment_on_a_different_webhook_reservation_freezes(client):
    conn, _ = held_order(order_id="order_first")
    _, ref = capture_ref(conn, order_id="order_first", payment_id="pay_shared",
                         key="first-capture")
    assert ledger.settle_capture(conn, ref, result={"id": "pay_shared"}) == "committed"
    conn.close()

    conn, _ = held_order(order_id="order_second")
    r = signed(client, payment_event(order_id="order_second", payment_id="pay_shared"),
               "evt_reused_payment")
    block = ledger.snapshot(conn, CALLER)
    assert r.json()["reason"] == "payment_already_committed_elsewhere"
    assert (block.spent, block.held) == (50000, 50000)
    assert block.frozen_at is not None
    conn.close()


def test_unexpected_storage_failure_is_generic_500_and_not_leaked(client, monkeypatch):
    def broken(*_args, **_kwargs):
        raise OSError("private database path")

    monkeypatch.setattr(ledger, "reconcile_webhook", broken)
    r = signed(client, payment_event(), "evt_broken")
    assert r.status_code == 500 and r.json() == {"error": "internal error"}
    assert "private database path" not in r.text


def test_reconciliation_rolls_back_on_storage_failure(client):
    conn, _ = held_order()
    conn.execute("CREATE TRIGGER fail_webhook BEFORE UPDATE OF held ON blocks"
                 " BEGIN SELECT RAISE(FAIL, 'forced failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="forced failure"):
        ledger.reconcile_webhook(
            conn, "evt_rollback", "payment.captured",
            payment_event()["payload"]["payment"]["entity"])
    assert conn.execute("SELECT COUNT(*) FROM webhook_events").fetchone()[0] == 0
    assert (ledger.snapshot(conn, CALLER).spent, ledger.snapshot(conn, CALLER).held) == (0, 50000)
    conn.close()


def test_event_insert_failure_is_not_misreported_as_a_duplicate(client):
    conn = ledger.connect()
    conn.execute("CREATE TRIGGER fail_event BEFORE INSERT ON webhook_events"
                 " BEGIN SELECT RAISE(FAIL, 'forced insert failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="forced insert failure"):
        ledger.reconcile_webhook(conn, "evt_insert_failure", "payment.authorized", None)
    assert conn.execute("SELECT COUNT(*) FROM webhook_events").fetchone()[0] == 0
    conn.close()


def test_connect_migrates_existing_sqlite_files(tmp_path):
    db = tmp_path / "old.db"
    old = sqlite3.connect(db)
    old.executescript("""
        CREATE TABLE blocks (block_id TEXT);
        CREATE TABLE reservations (reservation_id TEXT);
        CREATE TABLE idempotency (caller_id TEXT);
    """)
    old.close()
    conn = ledger.connect(str(db))
    assert {"frozen_at", "freeze_reason"} <= {
        row["name"] for row in conn.execute("PRAGMA table_info(blocks)")}
    assert "payment_id" in {row["name"] for row in conn.execute("PRAGMA table_info(reservations)")}
    assert "outcome_unknown" in {
        row["name"] for row in conn.execute("PRAGMA table_info(reservations)")}
    assert "reservation_id" in {row["name"] for row in conn.execute("PRAGMA table_info(idempotency)")}
    conn.close()


def test_a_signed_body_that_is_not_a_json_object_is_400(client):
    """Valid JSON is not enough: a list parses and then has no event to read."""
    r = signed(client, None, body=b"[1, 2, 3]")
    assert r.status_code == 400


def test_an_event_with_no_event_name_is_handled_as_unknown(client):
    """Razorpay names the event; a delivery without one must not index into it."""
    r = signed(client, {"payload": {"payment": {"entity": {"id": "pay_1"}}}},
               event_id="evt_no_name")
    assert r.status_code == 200
