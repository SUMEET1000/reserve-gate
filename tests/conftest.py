
import pytest


@pytest.fixture(autouse=True)
def audit_to_tmp(tmp_path, monkeypatch):
    """Keep every test's audit records out of the repo's own audit.jsonl."""
    monkeypatch.setenv("RESERVE_GATE_AUDIT", str(tmp_path / "audit.jsonl"))
