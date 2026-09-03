"""Bearer auth on the HTTP transport.

The deployed URL is public and holds a live payment credential, so an
unauthenticated request must never reach the MCP layer.
"""
import asyncio
from datetime import timedelta

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src import ledger, server
from src.policy import Call
from src.upstream import UpstreamError

TOKEN = "test-token-not-a-real-secret"


async def _reached(scope, receive, send):
    """Stands in for the MCP app. Reaching it at all means auth let the request
    through, and it echoes back what survived the wrapper."""
    seen = dict(scope.get("headers") or []).get(b"authorization")
    await JSONResponse({"authorization": seen and seen.decode()})(scope, receive, send)


def client(monkeypatch, token=TOKEN, audit_to=None):
    if token is None:
        monkeypatch.delenv("RESERVE_GATE_TOKEN", raising=False)
    else:
        monkeypatch.setenv("RESERVE_GATE_TOKEN", token)
    monkeypatch.setenv("RESERVE_GATE_AUDIT", str(audit_to or "audit-test.jsonl"))
    return TestClient(server.bearer_auth(_reached))


@pytest.fixture(autouse=True)
def _audit_to_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("RESERVE_GATE_AUDIT", str(tmp_path / "audit.jsonl"))


@pytest.mark.parametrize("headers", [
    {},                                   # no token at all
    {"Authorization": "Bearer wrong"},    # wrong token
    {"Authorization": TOKEN},             # right token, no scheme
    {"Authorization": "Basic " + TOKEN},  # right token, wrong scheme
    {"Authorization": "bearer " + TOKEN},  # right token, wrong case
])
def test_rejects_bad_credentials(monkeypatch, tmp_path, headers):
    c = client(monkeypatch, audit_to=tmp_path / "a.jsonl")
    assert c.post("/mcp", json={}, headers=headers).status_code == 401


def test_valid_token_gets_through(monkeypatch, tmp_path):
    c = client(monkeypatch, audit_to=tmp_path / "a.jsonl")
    r = c.post("/mcp", json={}, headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200


def test_callers_authorization_is_stripped_before_forwarding(monkeypatch, tmp_path):
    """Only the proxy's own Razorpay credential may travel upstream."""
    c = client(monkeypatch, audit_to=tmp_path / "a.jsonl")
    r = c.post("/mcp", json={}, headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.json()["authorization"] is None


def test_blank_token_does_not_authorise_everyone(monkeypatch, tmp_path):
    """An unset token must not become an empty expected value that a blank
    header happens to match."""
    c = client(monkeypatch, token=None, audit_to=tmp_path / "a.jsonl")
    assert c.post("/mcp", json={}).status_code == 401
    assert c.post("/mcp", json={}, headers={"Authorization": "Bearer "}).status_code == 401


def test_health_is_open_and_needs_no_credential(monkeypatch, tmp_path):
    c = client(monkeypatch, audit_to=tmp_path / "a.jsonl")
    assert c.get("/health").status_code == 200


def test_rejection_is_recorded(monkeypatch, tmp_path):
    log = tmp_path / "a.jsonl"
    client(monkeypatch, audit_to=log).post("/mcp", json={})
    assert "auth_reject" in log.read_text(encoding="utf-8")


@pytest.mark.parametrize("external, host, ok", [
    (None,                        "localhost:8000",           True),
    (None,                        "evil.example",             False),
    ("reserve-gate.onrender.com", "reserve-gate.onrender.com", True),
    ("reserve-gate.onrender.com", "evil.example",             False),
])
def test_host_allowlist(monkeypatch, external, host, ok):
    """DNS-rebinding protection stays on. The public hostname is declared, not
    disabled, so a request arriving under any other Host is still refused."""
    if external:
        monkeypatch.setenv("RENDER_EXTERNAL_HOSTNAME", external)
    else:
        monkeypatch.delenv("RENDER_EXTERNAL_HOSTNAME", raising=False)
    allowed = server._allowed_hosts()
    matched = host in allowed or any(
        a.endswith(":*") and host.startswith(a[:-2] + ":") for a in allowed)
    assert matched is ok


ADMIN = "test-admin-token-not-the-agents"


@pytest.mark.parametrize("path", ["/approve/abc", "/revoke/abc", "/block"])
def test_the_agent_token_cannot_reach_the_operator_routes(monkeypatch, tmp_path, path):
    """G12. The agent must hold RESERVE_GATE_TOKEN to reach /mcp at all, so
    guarding the approval gate with the same secret would let it approve its own
    spending and the human gate would be decoration."""
    c = client(monkeypatch, audit_to=tmp_path / "a.jsonl")
    monkeypatch.setenv("RESERVE_GATE_ADMIN_TOKEN", ADMIN)
    assert c.post(path, headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 401
    assert c.post(path, headers={"Authorization": f"Bearer {ADMIN}"}).status_code == 200


def test_the_admin_token_is_not_a_second_agent_credential(monkeypatch, tmp_path):
    """It opens the operator routes and nothing else; /mcp still needs its own."""
    c = client(monkeypatch, audit_to=tmp_path / "a.jsonl")
    monkeypatch.setenv("RESERVE_GATE_ADMIN_TOKEN", ADMIN)
    assert c.post("/mcp", json={}, headers={"Authorization": f"Bearer {ADMIN}"}).status_code == 401


def test_an_unset_admin_token_refuses_instead_of_opening(monkeypatch, tmp_path):
    c = client(monkeypatch, audit_to=tmp_path / "a.jsonl")
    monkeypatch.delenv("RESERVE_GATE_ADMIN_TOKEN", raising=False)
    assert c.post("/revoke/abc", headers={"Authorization": "Bearer "}).status_code == 401


def test_unfreeze_is_admin_only_and_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("RESERVE_GATE_DB", str(tmp_path / "unfreeze.db"))
    monkeypatch.setenv("RESERVE_GATE_TOKEN", TOKEN)
    monkeypatch.setenv("RESERVE_GATE_ADMIN_TOKEN", ADMIN)
    conn = ledger.connect()
    caller = server.caller_id()
    block_id = ledger.init(conn, server.config(), caller_id=caller)
    conn.execute("UPDATE blocks SET frozen_at = ?, freeze_reason = ? WHERE block_id = ?",
                 (ledger.iso(ledger.now_utc()), "test conflict", block_id))
    conn.close()

    app = Starlette(routes=[Route("/unfreeze/{block_id}", server.unfreeze, methods=["POST"])])
    with TestClient(server.bearer_auth(app)) as c:
        path = "/unfreeze/" + block_id
        assert c.post(path, headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 401
        assert c.post(path, headers={"Authorization": f"Bearer {ADMIN}"}).json() == {
            "block_id": block_id, "unfrozen": True}
        assert c.post(path, headers={"Authorization": f"Bearer {ADMIN}"}).json() == {
            "block_id": block_id, "unfrozen": False}
        assert c.post("/unfreeze/missing",
                      headers={"Authorization": f"Bearer {ADMIN}"}).status_code == 404

    conn = ledger.connect()
    block = ledger.snapshot(conn, caller)
    assert block.frozen_at is None and block.freeze_reason is None
    conn.close()


@pytest.mark.parametrize("known, held_after", [(True, 0), (False, 180000)])
def test_only_a_known_refusal_hands_the_hold_back(monkeypatch, tmp_path, known, held_after):
    """G14 and B25b. Razorpay answering with a refusal proves the call did not
    happen, so the hold returns to the block. A timeout proves nothing — Razorpay
    may have captured and lost the reply — so releasing there would hand back a
    balance that was really spent and the next call would spend it again."""
    monkeypatch.setenv("RESERVE_GATE_DB", str(tmp_path / "ledger.db"))
    conn = ledger.connect()
    caller = server.caller_id()
    ledger.init(conn, server.config(), caller_id=caller)
    call = Call(tool="create_order", caller_id=caller, amount=180000, currency="INR")
    decision, ref = ledger.authorize(conn, call, server.config())
    assert decision.allowed and ledger.snapshot(conn, caller).held == 180000

    async def refuses(_tool, _args):
        raise UpstreamError("upstream said no", known=known)

    monkeypatch.setattr(server, "call_razorpay", refuses)
    with pytest.raises(ValueError):
        asyncio.run(server._settle(conn, "create_order", ref, {}))
    assert ledger.snapshot(conn, caller).held == held_after
    conn.close()


def test_an_unknown_capture_cannot_expire_before_reconciliation(monkeypatch, tmp_path):
    monkeypatch.setenv("RESERVE_GATE_DB", str(tmp_path / "unknown-capture.db"))
    conn = ledger.connect()
    cfg, caller, now = server.config(), server.caller_id(), ledger.now_utc()
    ledger.init(conn, cfg, caller_id=caller, now=now)
    _, order_ref = ledger.authorize(
        conn, Call("create_order", caller, 50000, "INR", idem_key="unknown-order"),
        cfg, now=now)
    ledger.settle_order(conn, order_ref, order_id="order_unknown",
                        result={"id": "order_unknown"})
    capture = Call("capture_payment", caller, 50000, "INR", order_id="order_unknown",
                   payment_id="pay_unknown", idem_key="unknown-capture")
    _, capture_ref = ledger.authorize(conn, capture, cfg, now=now)

    async def times_out(_tool, _args):
        raise UpstreamError("reply lost", known=False)

    monkeypatch.setattr(server, "call_razorpay", times_out)
    with pytest.raises(ValueError, match="reply lost"):
        asyncio.run(server._settle(conn, "capture_payment", capture_ref, {}))

    later = now + timedelta(minutes=cfg.reservation_ttl_minutes, seconds=1)
    ledger.authorize(conn, Call("create_order", caller, 100, "INR", idem_key="expiry-probe"),
                     cfg, now=later)
    reservation = conn.execute(
        "SELECT state, outcome_unknown FROM reservations WHERE reservation_id = ?",
        (capture_ref.reservation_id,)).fetchone()
    assert (reservation["state"], reservation["outcome_unknown"]) == ("held", 1)

    result = ledger.reconcile_webhook(
        conn, "evt_unknown", "payment.captured",
        {"id": "pay_unknown", "order_id": "order_unknown", "amount": 50000,
         "currency": "INR", "status": "captured"}, now=later)
    assert result["effect"] == "APPLY"
    assert ledger.snapshot(conn, caller).spent == 50000
    conn.close()


def test_derived_keys_ignore_reworded_order_labels(monkeypatch, tmp_path):
    monkeypatch.setenv("RESERVE_GATE_DB", str(tmp_path / "labelled-orders.db"))
    caller, calls = server.caller_id(), []

    async def upstream(_tool, args):
        calls.append(args)
        return {"id": f"order_{len(calls)}"}

    monkeypatch.setattr(server, "call_razorpay", upstream)
    call = Call("create_order", caller, 50000, "INR")
    first = asyncio.run(server._gated(
        call, {"amount": 50000, "currency": "INR", "receipt": "keyboard"}))
    second = asyncio.run(server._gated(
        call, {"amount": 50000, "currency": "INR", "receipt": "monitor"}))
    assert (first["id"], second["id"]) == ("order_1", "order_1")
    assert [args["receipt"] for args in calls] == ["keyboard"]

    keyed = Call("create_order", caller, 50000, "INR", idem_key="labelled-client-call")
    asyncio.run(server._gated(
        keyed, {"amount": 50000, "currency": "INR", "receipt": "keyboard"}))
    with pytest.raises(ValueError, match="G16"):
        asyncio.run(server._gated(
            keyed, {"amount": 50000, "currency": "INR", "receipt": "monitor"}))
    assert len(calls) == 2


@pytest.mark.parametrize("payment", [
    {"order_id": "order_capture", "amount": 50001, "currency": "INR"},
    {"order_id": "order_capture", "amount": 50000, "currency": "USD"},
])
def test_capture_arguments_must_match_razorpays_payment(monkeypatch, tmp_path, payment):
    monkeypatch.setenv("RESERVE_GATE_DB", str(tmp_path / "capture.db"))
    monkeypatch.setenv("RESERVE_GATE_TOKEN", TOKEN)
    conn = ledger.connect()
    caller = server.caller_id()
    ledger.init(conn, server.config(), caller_id=caller)
    _, ref = ledger.authorize(
        conn, Call("create_order", caller, 50000, "INR", idem_key="create-capture"),
        server.config())
    ledger.settle_order(conn, ref, order_id="order_capture", result={"id": "order_capture"})
    conn.close()
    calls = []

    async def upstream(tool, args):
        calls.append((tool, args))
        return payment

    monkeypatch.setattr(server, "call_razorpay", upstream)
    with pytest.raises(ValueError, match=r"BLOCK \[R0\]"):
        asyncio.run(server.capture_payment("pay_capture", 50000, "INR"))
    assert [tool for tool, _ in calls] == ["fetch_payment"]
    conn = ledger.connect()
    assert (ledger.snapshot(conn, caller).spent, ledger.snapshot(conn, caller).held) == (0, 50000)
    conn.close()


def test_approval_is_operator_only_and_single_use(monkeypatch, tmp_path):
    monkeypatch.setenv("RESERVE_GATE_DB", str(tmp_path / "approve.db"))
    monkeypatch.setenv("RESERVE_GATE_TOKEN", TOKEN)
    monkeypatch.setenv("RESERVE_GATE_ADMIN_TOKEN", ADMIN)
    caller = server.caller_id()
    conn = ledger.connect()
    ledger.init(conn, server.config(), caller_id=caller)
    decision, ref = ledger.authorize(
        conn, Call("create_order", caller, 300000, "INR", idem_key="approval"),
        server.config())
    conn.close()
    server._HOLDS[ref.reservation_id] = (
        "create_order", {"amount": 300000, "currency": "INR"}, ref)
    calls = []

    async def upstream(tool, args):
        calls.append((tool, args))
        return {"id": "order_approved"}

    monkeypatch.setattr(server, "call_razorpay", upstream)
    app = Starlette(routes=[Route("/approve/{call_id}", server.approve, methods=["POST"])])
    with TestClient(server.bearer_auth(app)) as c:
        path = "/approve/" + ref.reservation_id
        assert c.post(path, headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 401
        assert c.post(path, headers={"Authorization": f"Bearer {ADMIN}"}).status_code == 200
        assert c.post(path, headers={"Authorization": f"Bearer {ADMIN}"}).status_code == 404
    assert len(calls) == 1


def test_expired_approval_never_reaches_upstream(monkeypatch, tmp_path):
    monkeypatch.setenv("RESERVE_GATE_DB", str(tmp_path / "expired-approve.db"))
    monkeypatch.setenv("RESERVE_GATE_TOKEN", TOKEN)
    monkeypatch.setenv("RESERVE_GATE_ADMIN_TOKEN", ADMIN)
    caller = server.caller_id()
    conn = ledger.connect()
    ledger.init(conn, server.config(), caller_id=caller)
    _, ref = ledger.authorize(
        conn, Call("create_order", caller, 300000, "INR", idem_key="expired-approval"),
        server.config())
    conn.execute("UPDATE reservations SET expires_at = '2000-01-01T00:00:00.000000Z'"
                 " WHERE reservation_id = ?", (ref.reservation_id,))
    conn.close()
    server._HOLDS[ref.reservation_id] = (
        "create_order", {"amount": 300000, "currency": "INR"}, ref)

    async def must_not_run(_tool, _args):
        raise AssertionError("expired approval reached upstream")

    monkeypatch.setattr(server, "call_razorpay", must_not_run)
    app = Starlette(routes=[Route("/approve/{call_id}", server.approve, methods=["POST"])])
    with TestClient(server.bearer_auth(app)) as c:
        r = c.post("/approve/" + ref.reservation_id,
                   headers={"Authorization": f"Bearer {ADMIN}"})
    assert r.status_code == 410


@pytest.mark.parametrize("kill", [
    "UPDATE blocks SET revoked_at = '2026-01-01T00:00:00.000000Z'",
    "UPDATE blocks SET expires_at = '2000-01-01T00:00:00.000000Z'",
    "UPDATE blocks SET frozen_at = '2026-01-01T00:00:00.000000Z'",
])
def test_a_hold_is_not_approvable_once_its_block_dies(monkeypatch, tmp_path, kill):
    """R4/R2. An approval is a fresh money call, so the block has to be live when
    it is approved. Checking only the reservation let a revoked or expired block
    still reach Razorpay through a hold taken while it was alive."""
    monkeypatch.setenv("RESERVE_GATE_DB", str(tmp_path / "dead-block.db"))
    monkeypatch.setenv("RESERVE_GATE_TOKEN", TOKEN)
    monkeypatch.setenv("RESERVE_GATE_ADMIN_TOKEN", ADMIN)
    caller = server.caller_id()
    conn = ledger.connect()
    ledger.init(conn, server.config(), caller_id=caller)
    _, ref = ledger.authorize(
        conn, Call("create_order", caller, 300000, "INR", idem_key="dead-block"),
        server.config())
    conn.execute(kill)
    conn.commit()
    conn.close()
    server._HOLDS[ref.reservation_id] = (
        "create_order", {"amount": 300000, "currency": "INR"}, ref)

    async def must_not_run(_tool, _args):
        raise AssertionError("a dead block reached upstream through an approval")

    monkeypatch.setattr(server, "call_razorpay", must_not_run)
    app = Starlette(routes=[Route("/approve/{call_id}", server.approve, methods=["POST"])])
    with TestClient(server.bearer_auth(app)) as c:
        r = c.post("/approve/" + ref.reservation_id,
                   headers={"Authorization": f"Bearer {ADMIN}"})
    assert r.status_code == 410
    conn = ledger.connect()
    assert ledger.snapshot(conn, caller).spent == 0
    conn.close()


def test_a_live_block_still_approves(monkeypatch, tmp_path):
    """The control for the three cases above: nothing killed, the hold executes."""
    monkeypatch.setenv("RESERVE_GATE_DB", str(tmp_path / "live-block.db"))
    monkeypatch.setenv("RESERVE_GATE_TOKEN", TOKEN)
    monkeypatch.setenv("RESERVE_GATE_ADMIN_TOKEN", ADMIN)
    caller = server.caller_id()
    conn = ledger.connect()
    ledger.init(conn, server.config(), caller_id=caller)
    _, ref = ledger.authorize(
        conn, Call("create_order", caller, 300000, "INR", idem_key="live-block"),
        server.config())
    conn.close()
    server._HOLDS[ref.reservation_id] = (
        "create_order", {"amount": 300000, "currency": "INR"}, ref)
    forwarded = []

    async def upstream(tool, args):
        forwarded.append(tool)
        return {"id": "order_live"}

    monkeypatch.setattr(server, "call_razorpay", upstream)
    app = Starlette(routes=[Route("/approve/{call_id}", server.approve, methods=["POST"])])
    with TestClient(server.bearer_auth(app)) as c:
        r = c.post("/approve/" + ref.reservation_id,
                   headers={"Authorization": f"Bearer {ADMIN}"})
    assert r.status_code == 200 and forwarded == ["create_order"]


def test_two_equal_tokens_refuse_to_serve(monkeypatch):
    """G12 is privilege separation. Two names for one secret is one credential,
    and the agent would hold the key to its own approval gate."""
    monkeypatch.setenv("RESERVE_GATE_TOKEN", "same-secret")
    monkeypatch.setenv("RESERVE_GATE_ADMIN_TOKEN", "same-secret")
    monkeypatch.setattr("sys.argv", ["reserve-gate", "--http"])
    # Stubbed so that a main() which fails to refuse ends the test instead of
    # binding a port and hanging it.
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    with pytest.raises(SystemExit, match="equal"):
        server.main()


def test_two_different_tokens_are_accepted(monkeypatch):
    """The control: distinct secrets get past the startup check and only the
    absence of a real server stops it, never the credential comparison."""
    monkeypatch.setenv("RESERVE_GATE_TOKEN", TOKEN)
    monkeypatch.setenv("RESERVE_GATE_ADMIN_TOKEN", ADMIN)
    monkeypatch.setattr("sys.argv", ["reserve-gate", "--http"])
    served = []
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: served.append(True))
    server.main()
    assert served == [True]


def test_http_startup_loads_the_local_dotenv_before_checking_tokens(monkeypatch):
    monkeypatch.delenv("RESERVE_GATE_TOKEN", raising=False)
    monkeypatch.delenv("RESERVE_GATE_ADMIN_TOKEN", raising=False)
    monkeypatch.setattr("sys.argv", ["reserve-gate", "--http"])
    loaded = []

    def local_env(*, override):
        loaded.append(override)
        monkeypatch.setenv("RESERVE_GATE_TOKEN", TOKEN)
        monkeypatch.setenv("RESERVE_GATE_ADMIN_TOKEN", ADMIN)

    monkeypatch.setattr(server, "load_dotenv", local_env)
    import uvicorn
    served = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: served.append(True))
    server.main()
    assert loaded == [False] and served == [True]


def test_a_refused_capture_is_not_handed_back_as_a_success(monkeypatch, tmp_path):
    """G4. The ledger froze the block and refused the debit, so returning
    Razorpay's own `status: captured` payload would tell the caller the money
    moved exactly as asked."""
    monkeypatch.setenv("RESERVE_GATE_DB", str(tmp_path / "refused-capture.db"))
    monkeypatch.setenv("RESERVE_GATE_TOKEN", TOKEN)
    conn = ledger.connect()
    caller = server.caller_id()
    ledger.init(conn, server.config(), caller_id=caller)
    _, ref = ledger.authorize(
        conn, Call("create_order", caller, 50000, "INR", idem_key="refused-capture"),
        server.config())
    ledger.settle_order(conn, ref, order_id="order_refused", result={"id": "order_refused"})
    conn.close()

    async def upstream(tool, args):
        if tool == "fetch_payment":
            return {"id": "pay_expected", "order_id": "order_refused",
                    "amount": 50000, "currency": "INR"}
        # Razorpay answers about a different payment than the one reserved.
        return {"id": "pay_other", "order_id": "order_refused", "amount": 50000,
                "currency": "INR", "status": "captured"}

    monkeypatch.setattr(server, "call_razorpay", upstream)
    with pytest.raises(ValueError, match="capture refused"):
        asyncio.run(server.capture_payment("pay_expected", 50000, "INR"))
    conn = ledger.connect()
    snap = ledger.snapshot(conn, caller)
    conn.close()
    assert snap.spent == 0 and snap.frozen_at is not None


def test_a_matching_capture_is_still_handed_back(monkeypatch, tmp_path):
    """The control: same shape, the payment Razorpay was asked about."""
    monkeypatch.setenv("RESERVE_GATE_DB", str(tmp_path / "ok-capture.db"))
    monkeypatch.setenv("RESERVE_GATE_TOKEN", TOKEN)
    conn = ledger.connect()
    caller = server.caller_id()
    ledger.init(conn, server.config(), caller_id=caller)
    _, ref = ledger.authorize(
        conn, Call("create_order", caller, 50000, "INR", idem_key="ok-capture"),
        server.config())
    ledger.settle_order(conn, ref, order_id="order_ok", result={"id": "order_ok"})
    conn.close()

    async def upstream(tool, args):
        return {"id": "pay_ok", "order_id": "order_ok", "amount": 50000,
                "currency": "INR", "status": "captured"}

    monkeypatch.setattr(server, "call_razorpay", upstream)
    result = asyncio.run(server.capture_payment("pay_ok", 50000, "INR"))
    assert result["id"] == "pay_ok"
    conn = ledger.connect()
    assert ledger.snapshot(conn, caller).spent == 50000
    conn.close()


@pytest.mark.parametrize("kill, rule", [("revoke", r"R4"), ("expire", r"R2"), ("freeze", r"G4")])
def test_a_block_that_dies_before_the_forward_stops_the_order(monkeypatch, tmp_path, kill, rule):
    """The reservation is committed before the upstream call, so a revocation, an
    expiry or a freeze landing in that gap has to stop the forward. Deciding once
    at authorize() left the ordinary path open after renew_hold closed it for a
    HOLD, and R4 says every later money call refuses immediately."""
    monkeypatch.setenv("RESERVE_GATE_DB", str(tmp_path / f"window-{kill}.db"))
    conn = ledger.connect()
    cfg, caller = server.config(), server.caller_id()
    ledger.init(conn, cfg, caller_id=caller)
    _, ref = ledger.authorize(conn, Call("create_order", caller, 180000, "INR"), cfg)
    block = ledger.snapshot(conn, caller)
    assert block.held == 180000

    if kill == "revoke":
        ledger.revoke(conn, block.block_id)
    else:
        column = "expires_at" if kill == "expire" else "frozen_at"
        when = ledger.now_utc() - timedelta(seconds=1)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(f"UPDATE blocks SET {column} = ? WHERE block_id = ?",
                     (ledger.iso(when), block.block_id))
        conn.execute("COMMIT")

    reached = []

    async def upstream(tool, _args):
        reached.append(tool)
        return {"id": "order_that_must_not_exist"}

    monkeypatch.setattr(server, "call_razorpay", upstream)
    with pytest.raises(ValueError, match=rule):
        asyncio.run(server._settle(conn, "create_order", ref, {}))
    assert reached == []
    # Nothing was sent, so the hold is not owed to anything and goes back.
    assert ledger.snapshot(conn, caller).held == 0
    conn.close()


def test_a_live_block_still_forwards(monkeypatch, tmp_path):
    """The control for the test above: the recheck must refuse a dead block and
    nothing else, or it would refuse every honest call and still look green."""
    monkeypatch.setenv("RESERVE_GATE_DB", str(tmp_path / "window-live.db"))
    conn = ledger.connect()
    cfg, caller = server.config(), server.caller_id()
    ledger.init(conn, cfg, caller_id=caller)
    _, ref = ledger.authorize(conn, Call("create_order", caller, 180000, "INR"), cfg)
    reached = []

    async def upstream(tool, _args):
        reached.append(tool)
        return {"id": "order_live"}

    monkeypatch.setattr(server, "call_razorpay", upstream)
    assert asyncio.run(server._settle(conn, "create_order", ref, {}))["id"] == "order_live"
    assert reached == ["create_order"]
    conn.close()


def test_a_capture_stopped_in_the_window_keeps_its_hold(monkeypatch, tmp_path):
    """The order is real and may still be paid, so refusing the capture must not
    hand the money back — a webhook settling it afterwards would then spend a
    balance the block had already released."""
    monkeypatch.setenv("RESERVE_GATE_DB", str(tmp_path / "window-capture.db"))
    conn = ledger.connect()
    cfg, caller = server.config(), server.caller_id()
    ledger.init(conn, cfg, caller_id=caller)
    _, order_ref = ledger.authorize(
        conn, Call("create_order", caller, 50000, "INR", idem_key="window-order"), cfg)
    ledger.settle_order(conn, order_ref, order_id="order_window",
                        result={"id": "order_window"})
    _, capture_ref = ledger.authorize(
        conn, Call("capture_payment", caller, 50000, "INR", order_id="order_window",
                   payment_id="pay_window", idem_key="window-capture"), cfg)
    ledger.revoke(conn, ledger.snapshot(conn, caller).block_id)
    reached = []

    async def upstream(tool, _args):
        reached.append(tool)
        return {"id": "pay_window", "status": "captured"}

    monkeypatch.setattr(server, "call_razorpay", upstream)
    with pytest.raises(ValueError, match=r"R4"):
        asyncio.run(server._settle(conn, "capture_payment", capture_ref, {}))
    assert reached == []
    block = ledger.snapshot(conn, caller)
    assert (block.held, block.spent) == (50000, 0)
    conn.close()
