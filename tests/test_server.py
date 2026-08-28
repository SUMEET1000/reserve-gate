"""Bearer auth on the HTTP transport.

The deployed URL is public and holds a live payment credential, so an
unauthenticated request must never reach the MCP layer.
"""
import asyncio

import pytest
from starlette.responses import JSONResponse
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
