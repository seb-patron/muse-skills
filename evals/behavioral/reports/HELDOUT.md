# Held-out evaluation report

Revision rounds: 0/3

Status: locked. Three historical Muse-authored heads are frozen as held-out, but
their gold is pending Sebastian's human adjudication. The runner requires verified
human gold, one finalist label, and that finalist's frozen SHA-256 in addition to
`SPIKE_HELDOUT_READY=1`; no held-out model row has been run.

| Case | Status | Gold | Result |
| --- | --- | --- | --- |
| `pr2-review-loop` | pending-human-adjudication | withheld | not run |
| `pr4-cross-model-reference` | pending-human-adjudication | withheld | not run |
| `pr3-behavioral-baseline` | pending-human-adjudication | withheld | not run |

After adjudication, record evidence in [`ADJUDICATION_QUEUE.md`](../ADJUDICATION_QUEUE.md),
replace any disclosed cases before further tuning, freeze the finalist hash, and run
each held-out case once. Do not infer human gold from Terra, Sol, Luna, Claude, or
prior reviews.
