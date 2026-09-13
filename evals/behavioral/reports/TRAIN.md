# Train evaluation report

Revision rounds: 2/3

Status: complete. Frozen train `eval-pL4-2026-09-13T01:43:26` produced exactly 12
fully graded rows with zero execution errors in 26m12s. Promptfoo recorded four
full assertion passes and 705,274 Terra grading tokens. Candidate token/cost
telemetry remained unavailable and is not reported as zero.

The preceding infrastructure probe
`eval-T5V-2026-09-13T01:27:21` completed `spike-current` on
`pr1-broken-promptfoo` in 4m09s. Muse exited 0 after 216,041ms with the pinned Spark
model and observed skill activation, then all three Terra rubrics completed (61,980
grading tokens). The normalized row has completion 1, errors 0, blocking/all-gold
recall 0, supported precision 1, and correct `NEEDS_FIXES` verdict. Candidate token
telemetry was absent rather than measured as zero. This wiring probe is not included
in the comparative train table below.

The run used one pass for each of the four hashed candidates on all three frozen
train cases, with the existing 24-step / 540-second Muse limits and independent
Terra-high grading. The earlier interrupted run remains diagnostic only: four Muse
calls completed before Promptfoo's deferred grader queue was interrupted during
call five.

| Candidate | Train rows | Fully graded | Blocking recall | All-gold recall | Supported precision | False approvals | Verdict accuracy | Latency | Candidate tokens/cost | Novel human-verified |
| --- | ---: | ---: | --- | --- | --- | ---: | --- | --- | --- | ---: |
| current | 3 | 3 | 0.667 | 0.333 | 1.000 | 1 | 0.667 | 138.8s | unavailable | 0 |
| minimal | 3 | 3 | 0.333 | 0.333 | 1.000 | 2 | 0.333 | 62.7s | unavailable | 0 |
| risk-first | 3 | 3 | 0.333 | 0.333 | 1.000 | 2 | 0.333 | 123.3s | unavailable | 0 |
| upstream-adapted | 3 | 3 | 0.333 | 0.333 | 1.000 | 2 | 0.333 | 93.9s | unavailable | 0 |

`spike-current` advances because it dominates the challengers on blocking recall,
false approvals, and verdict accuracy. The three challengers tie on every scored
quality metric; `spike-minimal` advances as the deterministic latency tie-breaker.
It averaged 62.7s versus 93.9s for `spike-upstream-adapted` and 123.3s for
`spike-risk-first`. The frozen validation pair is therefore `spike-current` and
`spike-minimal`. No candidate is promoted from train evidence alone.

A timeout is recorded as an execution error, not as zero recall. Raw results remain
under the ignored `results/` directory; this table was regenerated from their
normalized metrics.
