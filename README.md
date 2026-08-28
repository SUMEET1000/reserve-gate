# reserve-gate

A spending gate between an AI buyer and a merchant's Razorpay account. Every
money action is bounded by a reserved block, gated above an approval threshold,
refused with a stated reason, and written to an audit log.

See [ARCHITECTURE.md](ARCHITECTURE.md).

## Run it

```
pip install -r requirements.txt
pytest
python -m src.buyer --scripted --overspend
```

None of the three needs a Razorpay key, a model key or a network connection. The
last one runs the buyer agent against the gate over stdio and is refused six
times — by the per-call cap, the amount floor, the currency check, the approval
threshold and finally the block itself, which prints what is left of it.

Copy `.env.example` to `.env` to talk to Razorpay test mode.

Full documentation, the evaluation report and the connection instructions land
before submission.
