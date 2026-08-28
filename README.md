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

## The numbers

```
python harness/run_eval.py     # 130 adversarial cases -> eval_report.md
python harness/mutate.py       # deletes each rule and proves the cases notice
```

[eval_report.md](eval_report.md) is the first: 130 labelled attacks, **false-allow
0**, with the false-block cost reported beside it and the audit log's hash chain
verified over the records the run itself wrote.

The second is the answer to "were these tests written to pass?". It removes each
of the 15 guards from a copy of the policy in turn and re-scores the same cases.
Every rule has to be noticed, and the report names what caught the money when a
rule was gone — see [harness/mutation_report.md](harness/mutation_report.md).
[harness/provenance.md](harness/provenance.md) traces every attack class to a
source outside this repository, and names the one place that trace is still weak.

Copy `.env.example` to `.env` to talk to Razorpay test mode.

Full documentation and the connection instructions land before submission.
