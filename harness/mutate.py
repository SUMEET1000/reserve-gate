"""Switch off one rule at a time and prove the harness notices.

A suite that passes cannot tell you whether it is strong or whether it is
decoration - a check you can pass by reading the code is not a check. This turns
that around: it deletes each rule from a copy of `decide()` and re-scores the
same 130 cases. Every row must go red. A row that stays green means no case
depends on that rule, and the harness is not measuring it.

This control does not care who wrote the cases, which is the point. Answering
"were these tests written to pass?" by asking the author is circular; answering
it by removing the thing under test is not.

    python harness/mutate.py

The baseline row is the control: with nothing mutated the score must be clean,
or every red row below it could be explained by the harness simply being broken.
"""
import json
import os
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run_eval                                                     # noqa: E402
from src import audit, ledger                                       # noqa: E402

# Mutation runs write hundreds of audit records with a deliberately broken gate.
# They must not land in the file the real report verifies its chain over.
os.environ["RESERVE_GATE_AUDIT"] = os.path.join(tempfile.mkdtemp(), "mutation.jsonl")
audit._prev_hash = audit._prev_path = None

POLICY = HERE.parent / "src" / "policy.py"

# Each entry deletes exactly one guard by turning its condition off. The anchor
# is matched exactly once or the run aborts, so a refactor that moves a rule
# fails loudly here instead of silently mutating nothing and reporting a green
# row that means the opposite of what it looks like.
MUTATIONS = [
    ("R0 amount type", "if type(amount) is not int:"),
    ("R0 amount bounds", "if not MIN_AMOUNT <= amount <= MAX_AMOUNT:"),
    ("R0 currency", "if not isinstance(currency, str) or"
                    " currency.upper() != expected_currency.upper():"),
    ("R1 block cap", "if amount > block.available:"),
    ("R2 expiry", "if now >= block.expires_at:"),
    ("R3 already captured", 'if res.state == "committed":'),
    ("R3 no reservation", "if res is None:"),
    ("R4 revocation", "if block.revoked_at is not None:"),
    ("R5 per-call cap", "if amount > config.max_txn:"),
    ("R6 velocity", "if state.velocity_count >= config.velocity_calls:"),
    ("R7 replay", "if state.replay is not None:"),
    ("R7 in flight", "if state.in_flight:"),
    ("G15 tool allowlist", "if call.tool not in MONEY_TOOLS:"),
    ("G16 key conflict", "if state.conflict:"),
    ("approval hold", "if amount > config.approval_over:"),
]


def score_with(source: str, cases: list[dict]) -> dict:
    """Run the whole suite against one version of decide().

    ledger.py binds `decide` into its own namespace at import, so replacing that
    name is a complete swap - there is no second reference still pointing at the
    real rule.
    """
    namespace: dict = {}
    exec(compile(source, "<mutated policy>", "exec"), namespace)   # nosec B102
    real, ledger.decide = ledger.decide, namespace["decide"]
    try:
        res = run_eval.score(cases)
    finally:
        ledger.decide = real
    rows = res["rows"]
    # What refused the call once the rule was gone. A row that shows no
    # false-allow is only reassuring if something else can be named as having
    # stopped the money, so the substitute is reported rather than assumed.
    caught_by = sorted({r["got_rule"] or "ALLOW" for r in rows
                        if r["false_allow"] or r["wrong_rule"] or r["twin_diverged"]})
    return {"false_allow": sum(r["false_allow"] for r in rows),
            "false_block": sum(r["false_block"] for r in rows),
            "wrong_rule": sum(r["wrong_rule"] for r in rows),
            "twins": sum(r["twin_diverged"] for r in rows),
            "caught_by": ", ".join(caught_by) or "-"}


def main() -> int:
    original = POLICY.read_text(encoding="utf-8")
    text = (HERE / "cases.jsonl").read_text(encoding="utf-8")
    cases = [json.loads(ln) for ln in text.splitlines() if ln.strip()]

    base = score_with(original, cases)
    rows = [("baseline - nothing mutated", base, base["false_allow"] == 0
             and base["wrong_rule"] == 0 and base["twins"] == 0)]

    for label, anchor in MUTATIONS:
        if original.count(anchor) != 1:
            rows.append((label + "  [ANCHOR MISSING]", {}, False))
            continue
        got = score_with(original.replace(anchor, "if False:"), cases)
        noticed = got["false_allow"] + got["wrong_rule"] + got["twins"]
        rows.append((label, got, noticed > 0))

    width = max(len(r[0]) for r in rows)
    out = ["| rule removed | false-allow | wrong rule | twins | noticed | what caught it instead |",
           "|---|---|---|---|---|---|"]
    print("%-*s  %11s %10s %6s  %-8s %s" % (width, "rule removed", "false-allow",
                                            "wrong rule", "twins", "noticed",
                                            "caught by"))
    for label, got, ok in rows:
        mark = "yes" if ok else "NO"
        if label.startswith("baseline"):
            mark = "clean" if ok else "DIRTY"
        print("%-*s  %11s %10s %6s  %-8s %s" % (
            width, label, got.get("false_allow", "-"), got.get("wrong_rule", "-"),
            got.get("twins", "-"), mark, got.get("caught_by", "-")))
        out.append("| %s | %s | %s | %s | %s | %s |" % (
            label, got.get("false_allow", "-"), got.get("wrong_rule", "-"),
            got.get("twins", "-"), mark, got.get("caught_by", "-")))
    (HERE / "mutation_report.md").write_text(
        "# Mutation report\n\nGenerated by `python harness/mutate.py`. Each row deletes"
        " one guard\nfrom a copy of `decide()` and re-scores the same cases. Every row"
        " below the\nbaseline has to be noticed, or the harness is not measuring that"
        " rule.\n\nA row with no false-allow is not a weak row. It means a second"
        " control stopped\nthe money once the first was gone, and the last column names"
        " it. `G4` there is\nthe SQLite `CHECK (spent + held <= reserved)` constraint"
        " refusing the write -\nexactly the database-level backstop B07 asks for, doing"
        " its job with the\napplication logic deleted. The row to be alarmed by is one"
        " whose last column\nreads only `ALLOW`.\n\n"
        + "\n".join(out) + "\n", encoding="utf-8", newline="\n")

    failed = [label for label, _, ok in rows if not ok]
    print()
    print("all %d rules are measured by the harness" % len(MUTATIONS) if not failed
          else "NOT MEASURED: " + ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
