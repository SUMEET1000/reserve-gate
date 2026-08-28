"""G11. The audit log is the artefact the judging bar asks for, so it has to be
tamper-evident: a log anyone can edit in place proves nothing at all."""
import json

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
