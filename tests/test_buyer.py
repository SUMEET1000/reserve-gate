import os
import subprocess
import sys

from src.buyer import BASKETS, receipt_for


def test_every_basket_label_yields_a_receipt_razorpay_will_accept():
    """e5: `receipt` is 40 ASCII characters at most. The labels carry an em dash
    and one of them is long enough to need the cut, so both limits are live.
    """
    for label, _ in [item for basket in BASKETS.values() for item in basket]:
        receipt = receipt_for(label, "AbC-_9")
        assert len(receipt) <= 40, (label, receipt)
        assert receipt.isascii(), (label, receipt)


def test_two_runs_of_one_label_do_not_reuse_a_receipt():
    """Razorpay treats a repeated receipt as a duplicate, so a second demo run
    of the same basket must not present the first run's values.
    """
    assert receipt_for("monitor arm", "AbC-_9") != receipt_for("monitor arm", "zZ9-_x")


def test_default_overspend_is_reproducible_without_credentials_or_network():
    env = {
        key: value for key, value in os.environ.items()
        if key not in {
            "GEMINI_API_KEY",
            "RAZORPAY_KEY_ID",
            "RAZORPAY_KEY_SECRET",
            "RESERVE_GATE_DB",
        }
    }
    command = [sys.executable, "-m", "src.buyer", "--scripted", "--overspend"]

    for _ in range(2):
        result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=30)
        assert result.returncode == 0, result.stderr
        assert "6 of 6 calls refused by the gate." in result.stdout
