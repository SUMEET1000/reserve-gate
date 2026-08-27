# reserve-gate

A spending gate between an AI buyer and a merchant's Razorpay account. Every
money action is bounded by a reserved block, gated above an approval threshold,
refused with a stated reason, and written to an audit log.

See [ARCHITECTURE.md](ARCHITECTURE.md).

## Run it

```
pip install -r requirements.txt
pytest
```

Neither command needs a Razorpay key. Copy `.env.example` to `.env` to talk to
Razorpay test mode.

Full documentation, the evaluation report and the connection instructions land
before submission.
