"""Run every labelled attack in cases.jsonl against the real ledger and score it.

Each case gets its own in-memory ledger, so no case can poison the next and any
one of them can be re-run alone. Time is passed in rather than slept through, so
expiry, the reservation TTL and the rolling velocity window are all exercised in
milliseconds. Nothing here touches the network or reads a credential.

    python harness/run_eval.py            # writes eval_report.md

The number this exists to produce is false-allow. It is a property claim, so one
counterexample refutes it; false-block is reported beside it as the honest cost.
"""
import collections
import json
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

# Set before src.audit is imported, so the harness never appends to the repo's
# own audit.jsonl. The chain is then verified over this run's records, which is
# a few hundred real ones rather than a fixture.
AUDIT_LOG = HERE / "audit_run.jsonl"
os.environ["RESERVE_GATE_AUDIT"] = str(AUDIT_LOG)

from src import audit, ledger                                       # noqa: E402
from src.policy import ALLOW, Call, load_config                     # noqa: E402

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
CFG = load_config(str(ROOT / "policy.yaml"))
# Two callers, so a case can try to reach across one to the other. Derived from
# a token exactly as the server does; neither string is a real credential.
CALLERS = {k: ledger.caller_id_for("harness-token-" + k + "-not-a-secret")
           for k in ("a", "b")}

VERDICTS = ("ALLOW", "BLOCK", "HOLD")


def _call(spec: dict, caller: str) -> Call:
    return Call(tool=spec.get("tool", "create_order"), caller_id=CALLERS[caller],
                amount=spec.get("amount"), currency=spec.get("currency"),
                order_id=spec.get("order_id"), idem_key=spec.get("idem_key"))


def _step(conn, step: dict) -> None:
    """One setup action, run in the case's own ledger before the attack.

    Setup is how a case gets real history - five orders already held, a block
    already revoked, a key already spent - without sharing state with any other
    case.
    """
    caller = step.get("caller", "a")
    now = T0 + timedelta(seconds=step.get("at", 0))
    if step.get("revoke"):
        ledger.revoke(conn, ledger.snapshot(conn, CALLERS[caller]).block_id, now=now)
        return
    repeat = step.get("repeat", 1)
    for i in range(repeat):
        spec = dict(step["call"])
        if repeat > 1 and spec.get("idem_key"):
            spec["idem_key"] = spec["idem_key"] + "-" + str(i)
        _, ref = ledger.authorize(conn, _call(spec, caller), CFG, now=now)
        if ref is None:
            continue
        if step.get("settle"):
            oid = step["settle"] if repeat == 1 else step["settle"] + "-" + str(i)
            ledger.settle_order(conn, ref, order_id=oid, result={"id": oid})
        if step.get("capture"):
            ledger.settle_capture(conn, ref, result={"id": step["capture"]}, now=now)


def run_case(case: dict) -> tuple[str, str]:
    """Returns the outcome and the rule that decided it."""
    conn = ledger.connect(":memory:")
    try:
        for caller in CALLERS.values():
            ledger.init(conn, CFG, caller_id=caller, now=T0)
        for step in case.get("setup", []):
            _step(conn, step)
        decision, _ = ledger.authorize(
            conn, _call(case["call"], case.get("caller", "a")), CFG,
            now=T0 + timedelta(seconds=case.get("at", 1)))
        return decision.outcome, decision.rule
    finally:
        conn.close()


def score(cases: list[dict]) -> dict:
    rows, by_id = [], {}
    for case in cases:
        outcome, rule = run_case(case)
        want = case["expect"]
        row = {"id": case["id"], "class": case["class"], "source": case["source"],
               "want": want["outcome"], "want_rule": want.get("rule", ""),
               "got": outcome, "got_rule": rule, "note": case.get("note", ""),
               "twin_of": case.get("twin_of")}
        row["false_allow"] = want["outcome"] != ALLOW and outcome == ALLOW
        row["false_block"] = want["outcome"] == ALLOW and outcome != ALLOW
        row["wrong_rule"] = (outcome == want["outcome"]
                             and rule != want.get("rule", "")
                             and not row["false_allow"])
        rows.append(row)
        by_id[case["id"]] = row

    # An injection case has to decide *identically* to the clean call it shadows.
    # That is the actual claim - not that the payload is refused, but that the
    # gate never read it. A different rule firing is the failure to catch.
    for row in rows:
        twin = by_id.get(row["twin_of"] or "")
        row["twin_diverged"] = bool(
            twin and (row["got"], row["got_rule"]) != (twin["got"], twin["got_rule"]))
    return {"rows": rows,
            "matrix": collections.Counter((r["want"], r["got"]) for r in rows)}


def _table(rows: list[dict]) -> list[str]:
    if not rows:
        return ["None."]
    head = ["| id | class | expected | got | note |", "|---|---|---|---|---|"]
    return head + ["| `{id}` | {cls} | {w} {wr} | {g} {gr} | {n} |".format(
        id=r["id"], cls=r["class"], w=r["want"], wr=r["want_rule"],
        g=r["got"], gr=r["got_rule"], n=r["note"]) for r in rows]


def report(res: dict, chain_ok: bool, chain_line, tail) -> str:
    rows = res["rows"]
    fa = [r for r in rows if r["false_allow"]]
    fb = [r for r in rows if r["false_block"]]
    wr = [r for r in rows if r["wrong_rule"]]
    tw = [r for r in rows if r["twin_diverged"]]
    allowable = sum(1 for r in rows if r["want"] == ALLOW)
    chain = "verified" if chain_ok else "BROKEN at line " + str(chain_line)

    out = [
        "# Evaluation report - " + str(len(rows)) + " adversarial cases", "",
        "Generated by `python harness/run_eval.py`. Every case runs against the real",
        "ledger and the real `decide()`, on its own in-memory block, with no network and",
        "no credential. Regenerate this file rather than editing it.", "",
        "Run at `" + datetime.now(timezone.utc).isoformat(timespec="seconds")
        + "`, simulated clock `" + T0.isoformat() + "`.", "",
        "## The numbers", "",
        "- **False-allow: " + str(len(fa)) + ".** A call that had to be refused and was"
        " not. This is the claim the submission rests on, and one counterexample"
        " refutes it.",
        "- False-block: " + str(len(fb)) + " of " + str(allowable) + " calls that should"
        " have passed. The honest cost of the gate.",
        "- Right verdict, wrong rule: " + str(len(wr)) + ". Refused, but not by the rule"
        " that was meant to catch it - a control that has quietly stopped working.",
        "- Injection twins that diverged: " + str(len(tw)) + ". A payload that changed"
        " the decision would mean the gate read free text somewhere.",
        "- Audit chain over `harness/audit_run.jsonl`: " + chain + ".",
        "- Chain tail digest: `" + str(tail) + "`. The chain is unkeyed, so it catches an"
        " edited, deleted or reordered record but not a wholesale rewrite. This digest is"
        " what closes that: it is committed here, in git, beside the log it summarises, so"
        " a recomputed log no longer matches the value recorded next to it.", "",
        "## Confusion matrix", "",
        "| expected \\ actual | " + " | ".join(VERDICTS) + " |",
        "|---|" + "---|" * len(VERDICTS)]
    for want in VERDICTS:
        cells = [str(res["matrix"].get((want, got), 0)) for got in VERDICTS]
        out.append("| **" + want + "** | " + " | ".join(cells) + " |")

    out += ["", "## By attack class", "",
            "| class | cases | false-allow | false-block | wrong rule | source |",
            "|---|---|---|---|---|---|"]
    for cls in dict.fromkeys(r["class"] for r in rows):
        g = [r for r in rows if r["class"] == cls]
        out.append("| {c} | {n} | {a} | {b} | {w} | {s} |".format(
            c=cls, n=len(g), a=sum(r["false_allow"] for r in g),
            b=sum(r["false_block"] for r in g), w=sum(r["wrong_rule"] for r in g),
            s=g[0]["source"]))

    for title, group in (
            ("False-allow - every one of these refutes the claim", fa),
            ("False-block - calls refused that should have passed", fb),
            ("Right verdict, wrong rule", wr),
            ("Injection twins that diverged", tw)):
        out += ["", "## " + title, ""] + _table(group)
    return "\n".join(out) + "\n"


def main() -> int:
    AUDIT_LOG.unlink(missing_ok=True)
    audit._prev_hash = audit._prev_path = None      # a fresh chain for a fresh run
    text = (HERE / "cases.jsonl").read_text(encoding="utf-8")
    cases = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    res = score(cases)
    chain_ok, chain_line = audit.verify(str(AUDIT_LOG))
    tail = audit.tail_hash(str(AUDIT_LOG))
    (ROOT / "eval_report.md").write_text(report(res, chain_ok, chain_line, tail),
                                         encoding="utf-8", newline="\n")

    fa = sum(r["false_allow"] for r in res["rows"])
    fb = sum(r["false_block"] for r in res["rows"])
    wr = sum(r["wrong_rule"] for r in res["rows"])
    tw = sum(r["twin_diverged"] for r in res["rows"])
    print("{n} cases | false-allow {a} | false-block {b} | wrong-rule {w}"
          " | twins diverged {t} | chain {c}".format(
              n=len(cases), a=fa, b=fb, w=wr, t=tw,
              c="ok" if chain_ok else "BROKEN@" + str(chain_line)))
    for r in res["rows"]:
        if r["false_allow"] or r["wrong_rule"] or r["twin_diverged"]:
            print("  {i:<14} want {w} {wr:<8} got {g} {gr:<8} {n}".format(
                i=r["id"], w=r["want"], wr=r["want_rule"] or "-",
                g=r["got"], gr=r["got_rule"] or "-", n=r["note"]))
    # The exit code and the printed line come from the same four counts, so they
    # cannot disagree about whether the run passed.
    return 0 if (fa == 0 and wr == 0 and tw == 0 and chain_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
