"""G11. The audit log is the artefact the judging bar asks for, so it has to be
tamper-evident: a log anyone can edit in place proves nothing at all."""
import builtins
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from src import audit


@pytest.fixture
def log(tmp_path, monkeypatch):
    monkeypatch.setenv("RESERVE_GATE_AUDIT", str(tmp_path / "audit.jsonl"))
    # The chain tail is process state; each test starts its own file.
    monkeypatch.setattr(audit, "_prev_hash", None)
    monkeypatch.setattr(audit, "_prev_path", None)
    for i in range(5):
        audit.record(event="allow", tool="create_order", amount=1000 * (i + 1))
    return tmp_path / "audit.jsonl"


def lines(log):
    return log.read_text(encoding="utf-8").splitlines()


def rewrite(log, new_lines):
    log.write_text("\n".join(new_lines) + "\n", encoding="utf-8", newline="\n")


def test_a_clean_log_verifies(log):
    assert audit.verify(str(log)) == (True, None)


def test_every_record_links_to_the_one_before(log):
    recs = [json.loads(ln) for ln in lines(log)]
    assert recs[0]["prev_hash"] is None
    assert [r["prev_hash"] for r in recs[1:]] == [r["hash"] for r in recs[:-1]]


def test_an_edited_record_is_caught_and_named(log):
    """B22. The line number matters: 'something is wrong somewhere' is not a
    check a judge can act on."""
    edited = lines(log)
    edited[2] = edited[2].replace('"amount": 3000', '"amount": 30')
    rewrite(log, edited)
    assert audit.verify(str(log)) == (False, 3)


def test_a_deleted_record_is_caught(log):
    """The reason each record carries the previous digest and not just its own.
    A per-record hash cannot see a line that is no longer there — and quietly
    dropping the refusal is the edit an attacker actually wants."""
    kept = lines(log)
    del kept[2]
    rewrite(log, kept)
    assert audit.verify(str(log)) == (False, 3)


def test_records_reordered_are_caught(log):
    swapped = lines(log)
    swapped[1], swapped[2] = swapped[2], swapped[1]
    rewrite(log, swapped)
    assert audit.verify(str(log)) == (False, 2)


def test_the_chain_survives_a_restart(log, monkeypatch):
    """Render spins the instance down routinely. A restart has to continue the
    chain, not silently begin a second one inside the same file."""
    monkeypatch.setattr(audit, "_prev_hash", None)      # a fresh process
    monkeypatch.setattr(audit, "_prev_path", None)
    audit.record(event="allow", tool="capture_payment")
    assert audit.verify(str(log)) == (True, None)
    assert len(lines(log)) == 6


def test_a_second_process_appending_does_not_fork_the_chain(log):
    """One configured path is shared by the stdio server, a `--live` buyer run and
    the deployed process. Trusting a per-process tail gave two chains in one file
    and verify() named line 3 of a log nobody had touched."""
    subprocess.run([sys.executable, "-c",
                    "from src import audit; audit.record(event='child')"],
                   check=True, cwd=str(Path(__file__).resolve().parent.parent))
    audit.record(event="allow", tool="create_order", amount=7000)
    assert audit.verify(str(log)) == (True, None)
    assert len(lines(log)) == 7


def test_a_failed_write_does_not_poison_the_records_after_it(log):
    """The tail used to advance before the append, so a full disk left the cache
    pointing at a record no reader could see and every later record linked to it.
    The chain stayed broken once the disk recovered."""
    real_open = builtins.open

    def full_disk(path, *a, **kw):
        if str(path) == str(log) and "a" in (a[0] if a else kw.get("mode", "r")):
            raise OSError(28, "No space left on device")
        return real_open(path, *a, **kw)

    # Restored by hand rather than with monkeypatch.undo(), which would also roll
    # back the fixture's RESERVE_GATE_AUDIT and send the retry to another file.
    builtins.open = full_disk
    try:
        with pytest.raises(OSError):
            audit.record(event="allow", tool="create_order", amount=6000)
    finally:
        builtins.open = real_open

    audit.record(event="allow", tool="create_order", amount=6000)
    assert audit.verify(str(log)) == (True, None)
    assert len(lines(log)) == 6                 # the failed one wrote nothing


def test_an_unserialisable_record_still_chains(log):
    """record() must never raise — G4 would turn that into a refused money call
    — and the line it writes instead still has to carry the chain."""
    audit.record(event="odd", blob=object())
    assert audit.verify(str(log)) == (True, None)


def test_a_recomputed_log_defeats_verify_but_not_the_published_tail(log, tmp_path):
    """The honest limit of an unkeyed chain, and what actually closes it.

    verify() proves internal consistency. Someone who can rewrite the whole file
    can drop a refusal and recompute every digest, and verify() will pass - so
    the chain alone is not tamper-proof, only tamper-*evident* against partial
    edits. The control is the anchor: tail_hash() is published in eval_report.md
    and committed, so the forged log no longer matches the digest recorded
    beside it in git.

    An HMAC would not help. The key would sit next to the process writing the
    log, and verify() would need a secret a judge with a clone does not have.
    """
    published = audit.tail_hash(str(log))
    kept = [json.loads(ln) for ln in lines(log)][:3]      # a record quietly dropped

    prev, forged_lines = None, []
    for rec in kept:
        rec.pop("hash", None)
        rec["prev_hash"] = prev
        prev = audit._digest(rec)
        forged_lines.append(audit._canonical({**rec, "hash": prev}))
    forged = tmp_path / "forged.jsonl"
    forged.write_text("\n".join(forged_lines) + "\n", encoding="utf-8", newline="\n")

    assert audit.verify(str(forged)) == (True, None), "the chain alone cannot see this"
    assert audit.tail_hash(str(forged)) != published, "the published tail must"


def test_a_secret_inside_a_list_is_redacted(tmp_path, monkeypatch):
    """G6. Scrubbing walked dicts and strings; a token sitting in a list would
    have reached the log intact."""
    monkeypatch.setenv("RESERVE_GATE_AUDIT", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("RESERVE_GATE_TOKEN", "a-token-that-must-not-be-logged")
    monkeypatch.setattr(audit, "_prev_hash", None)
    monkeypatch.setattr(audit, "_prev_path", None)
    rec = audit.record(event="allow", trail=["clean", "a-token-that-must-not-be-logged"])
    assert rec["trail"] == ["clean", audit.REDACTED]
    assert "a-token-that-must-not-be-logged" not in json.dumps(rec)


def test_the_tail_of_a_file_ending_in_a_broken_line_is_none(tmp_path):
    """The tail seeds the next record's prev_hash. Guessing one off a corrupt
    line would chain a real record onto a value nothing can reproduce."""
    p = tmp_path / "a.jsonl"
    p.write_text("this line is not json\n", encoding="utf-8")
    assert audit.tail_hash(str(p)) is None


def test_a_record_that_cannot_be_serialised_degrades_instead_of_raising(tmp_path, monkeypatch):
    """G4. An audit write that raised would refuse an honest call, so a field
    that will not serialise becomes a marker and the chain stays intact.

    A plain object() is not enough: `_canonical` passes `default=str`, so almost
    anything renders. Only a value whose own rendering fails reaches the clause,
    which is what this stands in for.
    """
    class WillNotRender:
        def __str__(self):
            raise ValueError("this value cannot be rendered")
        __repr__ = __str__

    monkeypatch.setenv("RESERVE_GATE_AUDIT", str(tmp_path / "a.jsonl"))
    monkeypatch.setattr(audit, "_prev_hash", None)
    monkeypatch.setattr(audit, "_prev_path", None)
    rec = audit.record(event="allow", tool="create_order", bad=WillNotRender())
    assert rec["error"] == "record not serialisable"
    assert rec["event"] == "allow"
    assert audit.verify(str(tmp_path / "a.jsonl")) == (True, None)


def test_verifying_a_file_that_does_not_exist_is_false(tmp_path):
    """A judge running verify() against a missing log must be told so, not
    handed a True that means the loop never ran."""
    assert audit.verify(str(tmp_path / "no-such-log.jsonl")) == (False, None)


def test_a_blank_line_does_not_break_the_chain(log):
    rewrite(log, lines(log)[:2] + [""] + lines(log)[2:])
    assert audit.verify(str(log)) == (True, None)


def test_a_line_that_is_not_json_fails_at_its_own_line_number(log):
    """B22 reports where the log stopped being trustworthy, not merely that it did."""
    rewrite(log, lines(log)[:2] + ["{ not json"] + lines(log)[2:])
    assert audit.verify(str(log)) == (False, 3)


def test_concurrent_records_do_not_fork_the_chain(tmp_path, monkeypatch):
    """record() read-modify-writes the chain tail and then appends. Without one
    lock over both, two threads give two records the same prev_hash and verify()
    reports a log nobody tampered with as broken."""
    p = tmp_path / "threads.jsonl"
    monkeypatch.setenv("RESERVE_GATE_AUDIT", str(p))
    monkeypatch.setattr(audit, "_prev_hash", None)
    monkeypatch.setattr(audit, "_prev_path", None)
    before = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)               # make the interleave certain, not likely
    try:
        threads = [threading.Thread(target=lambda n=n: [audit.record(event="allow", t=n, i=i)
                                                        for i in range(25)])
                   for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        sys.setswitchinterval(before)
    assert len(lines(p)) == 200
    assert audit.verify(str(p)) == (True, None)


def test_the_admin_credential_is_redacted_like_the_agents(log, monkeypatch):
    """_live_secrets knew every credential except the one that releases a HOLD,
    so an upstream reply or a receipt echoing it stored it verbatim in the log
    the demo site publishes."""
    monkeypatch.setenv("RESERVE_GATE_ADMIN_TOKEN", "admin-secret-not-a-real-one")
    monkeypatch.setenv("RESERVE_GATE_TOKEN", "agent-secret-not-a-real-one")
    rec = audit.record(event="allow", receipt="echo admin-secret-not-a-real-one"
                                              " and agent-secret-not-a-real-one")
    assert "admin-secret-not-a-real-one" not in json.dumps(rec)
    assert "agent-secret-not-a-real-one" not in json.dumps(rec)
    assert rec["receipt"].count(audit.REDACTED) == 2


def test_records_deleted_from_the_end_are_caught_only_against_the_published_tail(log):
    """A truncated log is a valid chain that stops early, so nothing inside the
    file can see it. The digest committed to git is what can, and verify() has to
    be given it: the report used to claim a deleted record was caught either way."""
    published = audit.tail_hash(str(log))
    rewrite(log, lines(log)[:3])
    assert audit.verify(str(log)) == (True, None), "the chain alone cannot see this"
    assert audit.verify(str(log), expected_tail=published) == (False, 4)


def test_an_intact_log_still_passes_against_its_own_tail(log):
    """The control. The comparison must reject a short log and accept a whole one."""
    assert audit.verify(str(log), expected_tail=audit.tail_hash(str(log))) == (True, None)
