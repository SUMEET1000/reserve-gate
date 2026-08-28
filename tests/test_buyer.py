import os
import subprocess
import sys


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
