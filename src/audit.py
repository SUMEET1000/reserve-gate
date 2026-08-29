"""Append-only JSONL audit log, hash-chained.

One line per decision. This is the artefact the Track 01 bar asks for
("show the audit trail"), so it is written on every path including refusals.

Each record carries the previous record's digest (G11), so editing, deleting or
reordering one line breaks every link after it and verify() names the first one.

The chain is unkeyed, and that is a real limit rather than an oversight: anyone
who can rewrite the whole file can recompute every digest and produce a valid
chain. What stops that is the anchor, not the chain - `tail_hash` is published
in eval_report.md and both files are committed, so a recomputed log no longer
matches the digest recorded beside it in git history.

An HMAC would not close this. The key would have to live next to the process
writing the log, where an attacker who can rewrite the file already has it, and
verify() would then need a secret - so a judge with a clone could not check the
log at all, which is the only reason it exists.
"""
import hashlib
import json
import os
import threading
from datetime import datetime, timezone

# Any field whose name contains one of these is replaced wholesale. Values that
# merely *contain* a live secret are caught separately by _scrub.
SECRET_FIELDS = ("authorization", "secret", "token", "password", "key_id", "api_key")

REDACTED = "[redacted]"


def _live_secrets() -> list[str]:
    """Read lazily: importing this module must not require any credential."""
    return [v for v in (os.environ.get(n) for n in
                        ("RAZORPAY_KEY_SECRET", "RAZORPAY_KEY_ID",
                         "RAZORPAY_WEBHOOK_SECRET", "RESERVE_GATE_TOKEN",
                         "GEMINI_API_KEY")) if v]


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


def _canonical(rec: dict) -> str:
    return json.dumps(rec, sort_keys=True, ensure_ascii=False, default=str)


def _digest(rec: dict) -> str:
    """The digest of one record, taken over its canonical JSON and never over
    the file line. `core.autocrlf` is on by default on Windows, so a chain over
    raw line bytes verifies on this machine and fails on a judge's clone — the
    log reported as tampered by the very check that exists to prove it is not.
    """
    return hashlib.sha256(_canonical(rec).encode("utf-8")).hexdigest()


# The tail of the chain for this process, and the file it belongs to. The first
# write recovers it from disk, so a restart continues the chain rather than
# starting a second one in the middle of the same file.
_prev_hash: str | None = None
_prev_path: str | None = None
# record() read-modify-writes that tail and then appends. Two threads doing it
# at once hand two records the same prev_hash, which forks the chain and fails
# verify(). The two-thread ledger test already drives this path.
_lock = threading.Lock()


def tail_hash(p: str) -> str | None:
    # ponytail: reads the whole file once per process. Fine at demo scale; seek
    # backwards from the end if the log ever gets large.
    try:
        with open(p, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if line.strip():
            try:
                return json.loads(line).get("hash")
            except ValueError:
                return None
    return None


def record(**fields) -> dict:
    """Write one audit line and return it. Never raises on a serialisation
    problem: G4 turns any exception in the money path into a refusal, so a
    formatting fault in the log would refuse an honest call."""
    global _prev_hash, _prev_path
    with _lock:
        p = path()
        if _prev_path != p:
            _prev_hash, _prev_path = tail_hash(p), p

        rec = {"ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
               **_scrub(fields, _live_secrets()), "prev_hash": _prev_hash}
        try:
            line = _canonical({**rec, "hash": _digest(rec)})
        except (TypeError, ValueError):
            rec = {"ts": rec["ts"], "event": fields.get("event", "unknown"),
                   "error": "record not serialisable", "prev_hash": _prev_hash}
            line = _canonical({**rec, "hash": _digest(rec)})
        rec["hash"] = json.loads(line)["hash"]
        _prev_hash = rec["hash"]
        # encoding and newline are both explicit: the default on Windows is cp1252,
        # which raises on a rupee sign, and the default newline translation would
        # put CRLF in a file the chain is read back from.
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
    return rec


def verify(p: str | None = None) -> tuple[bool, int | None]:
    """Walk the chain. Returns (ok, the 1-based line number of the first bad
    record). B22: an edited record fails here rather than passing quietly.

    This proves internal consistency. It cannot prove the file was never
    rewritten wholesale - compare tail_hash() against the value published in
    eval_report.md and committed to git for that.
    """
    try:
        with open(p or path(), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return False, None
    prev = None
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            return False, n
        got = rec.pop("hash", None)
        if rec.get("prev_hash") != prev or got != _digest(rec):
            return False, n
        prev = got
    return True, None


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
    assert r["prev_hash"] is None, "the first record starts the chain"

    with open(path(), encoding="utf-8") as f:
        assert json.loads(f.read().strip())["amount"] == 50000

    second = record(event="call", tool="capture_payment")
    assert second["prev_hash"] == r["hash"], "each record links to the one before"
    record(event="unserialisable", blob=object())      # must not raise
    assert verify() == (True, None), verify()

    # B22. One edited byte in record 2 has to be found, and found there.
    with open(path(), encoding="utf-8") as f:
        lines = f.read().splitlines()
    lines[1] = lines[1].replace('"capture_payment"', '"create_order"')
    with open(path(), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    assert verify() == (False, 2), verify()
    print("audit self-check OK ->", path())
