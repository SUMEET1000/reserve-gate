"""Score the same 150 cases with every rule switched off, then with the gate on.

`mutate.py` deletes one rule at a time to prove the harness measures each of
them. That answers "are these tests real?" and it takes a table to explain. This
answers a different question in one line - "what does the gate actually stop?" -
by deleting all of them at once and totalling the money that then walks through.

    python harness/gate_off.py

The comparison only means something because both columns run the same cases
through the same ledger; the single difference is whether `decide()` still has
its guards. Exit is non-zero if the gate-on column is not clean, so this cannot
report a win by being broken.
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# mutate.py redirects RESERVE_GATE_AUDIT to a temp file at import, which is what
# keeps a run with the guards deleted out of the audit log the report verifies.
from mutate import MUTATIONS, POLICY                                # noqa: E402
import run_eval                                                     # noqa: E402
from src import ledger                                              # noqa: E402


def score_rows(source: str, cases: list[dict]) -> list[dict]:
    """Same swap `mutate.score_with` performs, but hands back the rows.

    Its own return value is a set of totals, and this needs each row's id to
    join it to the amount the case was carrying.
    """
    namespace: dict = {}
    exec(compile(source, "<mutated policy>", "exec"), namespace)   # nosec B102
    real, ledger.decide = ledger.decide, namespace["decide"]
    try:
        return run_eval.score(cases)["rows"]
    finally:
        ledger.decide = real


def rupees(paise: int) -> str:
    return "%s%s" % ("₹", "{:,}".format(round(paise / 100)))


def main() -> int:
    cases = [json.loads(ln) for ln
             in (HERE / "cases.jsonl").read_text(encoding="utf-8").splitlines()
             if ln.strip()]
    # The 20 webhook cases carry no `call`: they arrive at /webhook rather than
    # through decide(), so no rule this script deletes is on their path and
    # counting them would inflate both columns equally and mean nothing.
    money = [c for c in cases if "call" in c]
    # B04's malformed amounts are deliberately not integers ("50000", True, a
    # float). They are worth 0 rupees here rather than being coerced: the point
    # of those cases is the type, and inventing a value for one would put money
    # in the total that no call could ever have moved.
    amount_of = {c["id"]: c["call"]["amount"]
                 if type(c["call"].get("amount")) is int else 0
                 for c in money}
    # A case the suite expects to be refused is the money this gate exists to
    # stop. The negative controls are excluded here on purpose: letting a
    # legitimate purchase through is the gate working, not a leak.
    should_refuse = {c["id"] for c in money if c["expect"]["outcome"] != "ALLOW"}

    original = POLICY.read_text(encoding="utf-8")
    stripped = original
    for label, anchor in MUTATIONS:
        if original.count(anchor) != 1:
            print("anchor for %s no longer matches exactly once in policy.py" % label)
            return 2
        stripped = stripped.replace(anchor, "if False:")

    off = score_rows(stripped, cases)
    on = score_rows(original, cases)

    def leaked(rows: list[dict]) -> tuple[int, int]:
        got = [r for r in rows if r["false_allow"] and r["id"] in amount_of]
        return len(got), sum(amount_of[r["id"]] for r in got)

    off_n, off_paise = leaked(off)
    on_n, on_paise = leaked(on)
    # Something still refuses the rest once the policy is gone, and asserting
    # which control that was would be a guess. These are the rules the run
    # actually reported, so the sentence below is a measurement.
    survivors = sorted({r["got_rule"] or "schema" for r in off
                        if r["id"] in should_refuse and not r["false_allow"]})

    print("%d cases, %d of them money calls, %d of those the gate is meant to refuse\n"
          % (len(cases), len(money), len(should_refuse)))
    print("%-34s %14s %16s" % ("", "calls through", "money moved"))
    print("%-34s %14d %16s" % ("all %d guards deleted" % len(MUTATIONS),
                              off_n, rupees(off_paise)))
    print("%-34s %14d %16s" % ("gate on", on_n, rupees(on_paise)))
    print("\nthe other %d were still refused with all of them deleted, by: %s"
          % (len(should_refuse) - off_n, ", ".join(survivors)))

    if on_n:
        print("\ngate-on column is not clean - the comparison means nothing")
        return 1
    print("\n%d unauthorised money calls and %s got through with the guards off."
          "\n%d and %s got through with them on."
          % (off_n, rupees(off_paise), on_n, rupees(on_paise)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
