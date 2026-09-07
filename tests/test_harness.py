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

import perf_check                                                   # noqa: E402
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


@pytest.mark.parametrize("last_status, expected", [(200, False), (500, False)])
def test_a_run_of_failed_requests_never_reports_a_pass(monkeypatch, last_status, expected):
    """The status was overwritten every repetition, so eleven HTTP 500s followed
    by one 200 graded PASS and the budget check ran on the timings of failures."""
    monkeypatch.setattr(perf_check, "PROBES", [("GET", "/api/feed", None)])
    statuses = iter([200, 200] + [500] * 11 + [last_status])

    def hit(*_args):
        return 1.0, next(statuses), ""

    monkeypatch.setattr(perf_check, "hit", hit)
    rows = perf_check.measure_endpoints("http://local")
    passed, _ = perf_check.verdict(rows[0][1], 20, rows[0][2], rows[0][3])
    assert passed is expected


def test_a_run_with_no_failure_still_passes(monkeypatch):
    """The control. The row only fails because a measured request failed."""
    monkeypatch.setattr(perf_check, "PROBES", [("GET", "/api/feed", None)])
    monkeypatch.setattr(perf_check, "hit", lambda *_a: (1.0, 200, ""))
    rows = perf_check.measure_endpoints("http://local")
    assert perf_check.verdict(rows[0][1], 20, rows[0][2], rows[0][3])[0] is True


def test_a_verdict_that_is_neither_the_expected_one_nor_a_false_allow_is_counted():
    """An expected BLOCK answered with a HOLD set none of false_allow,
    false_block, wrong_rule or wrong_effect, so a wrong verdict was reported as
    a clean run and main() exited 0."""
    rows = run_eval.score([{
        "id": "probe", "class": "synthetic", "source": "test",
        "call": {"tool": "create_order", "amount": 300000, "currency": "INR"},
        "expect": {"outcome": "BLOCK", "rule": "R5"},
    }])["rows"]
    assert rows[0]["got"] == "HOLD" and rows[0]["want"] == "BLOCK"
    assert rows[0]["wrong_outcome"] is True
    assert not (rows[0]["false_allow"] or rows[0]["false_block"])


def test_a_verdict_that_matches_is_not_counted_as_wrong():
    """The control. The new flag must fire on a mismatch and on nothing else."""
    rows = run_eval.score([{
        "id": "probe", "class": "synthetic", "source": "test",
        "call": {"tool": "create_order", "amount": 300000, "currency": "INR"},
        "expect": {"outcome": "HOLD"},
    }])["rows"]
    assert rows[0]["got"] == "HOLD" and rows[0]["wrong_outcome"] is False
