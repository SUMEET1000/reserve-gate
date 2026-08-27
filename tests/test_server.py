"""Bearer auth on the HTTP transport.

The deployed URL is public and holds a live payment credential, so an
unauthenticated request must never reach the MCP layer.
"""
import pytest
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from src import server

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
