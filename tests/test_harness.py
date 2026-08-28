"""The 150 adversarial cases, run by pytest as well as by the report.

harness/run_eval.py produces the number for the submission. This runs the same
file, so a rule that breaks fails the suite too - not only a report somebody has
to remember to regenerate.
"""
import json
import pathlib
import sys

import pytest

HARNESS = pathlib.Path(__file__).resolve().parent.parent / "harness"
sys.path.insert(0, str(HARNESS))

import run_eval                                                     # noqa: E402

CASES = [json.loads(ln) for ln in
         HARNESS.joinpath("cases.jsonl").read_text(encoding="utf-8").splitlines()
         if ln.strip()]


def test_the_case_file_is_the_size_it_claims_to_be():
    assert len(CASES) == 150, "cases.jsonl and the README must agree on the count"
    assert len({c["id"] for c in CASES}) == 150, "duplicate case id"


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_case(case):
    outcome, rule, effect = run_eval.run_case(case)
    want = case["expect"]
    assert outcome == want["outcome"], case["note"]
    # The rule is asserted too. A refusal for the wrong reason goes quiet exactly
    # when the control it was testing disappears.
    assert rule == want.get("rule", ""), f"right verdict, wrong rule: {case['note']}"
    assert effect == want.get("effect"), f"wrong webhook effect: {case['note']}"
