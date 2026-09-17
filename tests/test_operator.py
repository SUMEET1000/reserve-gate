import asyncio

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from src import audit, ledger, operator, server
from src.policy import Call
from src.upstream import UpstreamError


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("RESERVE_GATE_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("RESERVE_GATE_TOKEN", "agent-fixture")
    monkeypatch.setenv("RESERVE_GATE_ADMIN_TOKEN", "operator-fixture")
    conn = ledger.connect()
    cfg = server.config()
    caller = server.caller_id()
    ledger.init(conn, cfg, caller_id=caller)
    _, ref = ledger.authorize(conn, Call("create_order", caller, 50000, "INR", idem_key="order"), cfg)
    ledger.settle_order(conn, ref, order_id="order_recovery", result={"id": "order_recovery"})
    _, capture = ledger.authorize(conn, Call("capture_payment", caller, 50000, "INR",
                                            order_id="order_recovery", payment_id="pay_recovery",
                                            idem_key="capture"), cfg)
    ledger.mark_outcome_pending(conn, capture)
    app = Starlette(routes=[Route("/operator/inbox", operator.inbox),
                            Route("/operator/recover/{reservation_id}", operator.recover, methods=["POST"]),
                            Route("/approve/{call_id}", server.approve, methods=["POST"]),
                            Route("/unfreeze/{block_id}", server.unfreeze, methods=["POST"])])
    with TestClient(server.bearer_auth(app)) as client:
        client.headers["Authorization"] = "Bearer operator-fixture"
        yield client, conn, capture, cfg, caller
    conn.close()


def test_inbox_is_private_and_excludes_pending_arguments(recovery):
    client, conn, ref, cfg, caller = recovery
    _, pending = ledger.authorize(conn, Call("create_order", caller, 300000, "INR", idem_key="approval"), cfg)
    ledger.park_hold(conn, pending, "create_order", {"amount": 300000, "currency": "INR", "notes": {"private": "hidden-note"}})
    block_id = ledger.snapshot(conn, caller).block_id
    conn.execute("UPDATE blocks SET frozen_at = ?, freeze_reason = 'conflict' WHERE block_id = ?",
                 (ledger.iso(ledger.now_utc()), block_id))
    for headers in ({"Authorization": ""}, {"Authorization": "Bearer agent-fixture"}):
        assert client.get("/operator/inbox", headers=headers).status_code == 401
        assert client.post("/operator/recover/" + ref.reservation_id, headers=headers).status_code == 401
    result = client.get("/operator/inbox")
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    body = result.json()
    assert len(body["reservations"]) == 2 and len(body["blocks"]) == 1
    assert len(body["blocks"][0]["history"]) == 2
    assert "hidden-note" not in result.text and "operator-fixture" not in result.text
    before = ledger.snapshot(conn, caller)
    assert client.post("/unfreeze/" + block_id).json()["unfrozen"] is True
    after = ledger.snapshot(conn, caller)
    assert (before.spent, before.held) == (after.spent, after.held)


@pytest.mark.parametrize("change, expected", [
    ({}, 200), ({"amount": 50001}, 409), ({"amount": True}, 409),
    ({"order_id": "order_other"}, 409), ({"id": "pay_other"}, 409),
    ({"currency": "USD"}, 409), ({"status": "authorized"}, 200),
    ({"status": "failed"}, 200), ({"status": None}, 409),
])
def test_recovery_requires_matching_captured_provider_evidence(recovery, monkeypatch, change, expected):
    client, conn, ref, cfg, caller = recovery
    calls = []
    payment = {"id": "pay_recovery", "order_id": "order_recovery", "amount": 50000,
               "currency": "INR", "status": "captured", "email": "private@example.test", **change}

    async def upstream(tool, args):
        calls.append((tool, args))
        return payment

    monkeypatch.setattr(operator, "call_razorpay", upstream)
    response = client.post("/operator/recover/" + ref.reservation_id)
    assert response.status_code == expected
    assert calls == [("fetch_payment", {"payment_id": "pay_recovery"})]
    assert "private@example.test" not in response.text
    block = ledger.snapshot(conn, caller)
    assert (block.spent, block.held) == ((50000, 0) if not change else (0, 50000))
    if not change:
        assert client.post("/operator/recover/" + ref.reservation_id).status_code == 409
        assert len(calls) == 1
        stored = ledger.completed_capture(conn, caller, "capture", "pay_recovery")
        assert stored["status"] == "captured"
        assert client.get("/operator/inbox").json()["reservations"] == []
    assert audit.verify()[0]


def test_lookup_failure_and_missing_reference_keep_funds(recovery, monkeypatch):
    client, conn, ref, cfg, caller = recovery

    async def unavailable(tool, args):
        raise UpstreamError("private upstream diagnostic", known=False)

    monkeypatch.setattr(operator, "call_razorpay", unavailable)
    response = client.post("/operator/recover/" + ref.reservation_id)
    assert response.status_code == 502 and "private upstream diagnostic" not in response.text
    conn.execute("UPDATE reservations SET payment_id = NULL WHERE reservation_id = ?", (ref.reservation_id,))
    assert client.post("/operator/recover/" + ref.reservation_id).status_code == 409
    assert ledger.snapshot(conn, caller).held == 50000
    assert client.post("/operator/recover/missing").status_code == 404


def test_frozen_or_concurrently_settled_payment_is_not_double_spent(recovery, monkeypatch):
    client, conn, ref, cfg, caller = recovery
    payment = {"id": "pay_recovery", "order_id": "order_recovery", "amount": 50000,
               "currency": "INR", "status": "captured"}

    async def concurrent_webhook(tool, args):
        racing = ledger.connect()
        try:
            event = "racing-frozen" if ledger.snapshot(racing, caller).frozen_at else "racing-unfrozen"
            ledger.reconcile_webhook(racing, event, "payment.captured", payment)
        finally:
            racing.close()
        return payment

    monkeypatch.setattr(operator, "call_razorpay", concurrent_webhook)
    block_id = ledger.snapshot(conn, caller).block_id
    conn.execute("UPDATE blocks SET frozen_at = ? WHERE block_id = ?", (ledger.iso(ledger.now_utc()), block_id))
    assert client.post("/operator/recover/" + ref.reservation_id).status_code == 409
    assert ledger.snapshot(conn, caller).held == 50000
    ledger.unfreeze(conn, block_id)
    result = client.post("/operator/recover/" + ref.reservation_id)
    assert result.status_code == 200
    assert ledger.snapshot(conn, caller).spent == 50000


def test_inbox_approval_survives_connection_close_and_cannot_repeat(recovery, monkeypatch):
    client, conn, ref, cfg, caller = recovery
    with pytest.raises(ValueError, match="HOLD"):
        asyncio.run(server._gated(Call("create_order", caller, 300000, "INR", idem_key="pending"),
                                  {"amount": 300000, "currency": "INR"}))
    pending = next(r for r in client.get("/operator/inbox").json()["reservations"] if r["awaiting_approval"])
    calls = []

    async def upstream(tool, args):
        calls.append(tool)
        return {"id": "order_approved", "amount": 300000, "currency": "INR"}

    monkeypatch.setattr(server, "call_razorpay", upstream)
    path = "/approve/" + pending["reservation_id"]
    assert client.post(path).status_code == 200
    assert client.post(path).status_code == 404 and calls == ["create_order"]
    assert all(not r["awaiting_approval"] for r in client.get("/operator/inbox").json()["reservations"])
