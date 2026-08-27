"""Append-only JSONL audit log.

One line per decision. This is the artefact the Track 01 bar asks for
("show the audit trail"), so it is written on every path including refusals.
The hash chain lands with the policy engine; today the record is the point.
"""
import json
import os
from datetime import datetime, timezone

# Any field whose name contains one of these is replaced wholesale. Values that
# merely *contain* a live secret are caught separately by _scrub.
SECRET_FIELDS = ("authorization", "secret", "token", "password", "key_id", "api_key")

REDACTED = "[redacted]"


def _live_secrets() -> list[str]:
    """Read lazily: importing this module must not require any credential."""
    return [v for v in (os.environ.get(n) for n in
                        ("RAZORPAY_KEY_SECRET", "RAZORPAY_KEY_ID",
                         "RESERVE_GATE_TOKEN", "GEMINI_API_KEY")) if v]


def _scrub(value, secrets: list[str]):
    if isinstance(value, dict):
        return {k: (REDACTED if any(s in k.lower() for s in SECRET_FIELDS)
                    else _scrub(v, secrets)) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v, secrets) for v in value]
    if isinstance(value, str):
        for s in secrets:
            value = value.replace(s, REDACTED)
        # An upstream payload can be arbitrarily large; a fetch_all_* reply has
        # been seen at thousands of rows. Truncate rather than fill the disk.
        return value if len(value) <= 2000 else value[:2000] + "...[truncated]"
    return value


def path() -> str:
    return os.environ.get("RESERVE_GATE_AUDIT", "audit.jsonl")


def record(**fields) -> dict:
    """Write one audit line and return it. Never raises on a serialisation
    problem: a broken log must not turn a legitimate call into a refusal."""
    rec = {"ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
           **_scrub(fields, _live_secrets())}
    try:
        line = json.dumps(rec, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        rec = {"ts": rec["ts"], "event": fields.get("event", "unknown"),
               "error": "record not serialisable"}
        line = json.dumps(rec, sort_keys=True)
    # encoding and newline are both explicit: the default on Windows is cp1252,
    # which raises on a rupee sign, and the default newline translation would
    # break the hash chain across machines.
    with open(path(), "a", encoding="utf-8", newline="\n") as f:
        f.write(line + "\n")
    return rec


if __name__ == "__main__":
    import tempfile
    os.environ["RESERVE_GATE_AUDIT"] = os.path.join(tempfile.mkdtemp(), "a.jsonl")
    os.environ["RESERVE_GATE_TOKEN"] = "sup3rsecret"

    r = record(event="call", tool="create_order", amount=50000,
               note="cap is \u20b910,000", headers={"Authorization": "Bearer x"},
               echo="token is sup3rsecret")
    assert r["headers"]["Authorization"] == REDACTED, r
    assert "sup3rsecret" not in json.dumps(r), r
    assert r["ts"].endswith("Z") and "T" in r["ts"], r

    with open(path(), encoding="utf-8") as f:
        assert json.loads(f.read().strip())["amount"] == 50000

    record(event="unserialisable", blob=object())  # must not raise
    print("audit self-check OK ->", path())
