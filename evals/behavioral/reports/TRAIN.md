# Train evaluation report

Revision rounds: 2/3

Status: exact infrastructure probe passed; frozen 12-row train pending. Probe
`eval-T5V-2026-09-13T01:27:21` completed `spike-current` on
`pr1-broken-promptfoo` in 4m09s. Muse exited 0 after 216,041ms with the pinned Spark
model and observed skill activation, then all three Terra rubrics completed (61,980
grading tokens). The normalized row has completion 1, errors 0, blocking/all-gold
recall 0, supported precision 1, and correct `NEEDS_FIXES` verdict. Candidate token
telemetry was absent rather than measured as zero. This wiring probe is not included
in the comparative train table below.

The repaired train command enforces 12 rows, one pass for each of the four hashed
candidates, with the existing 24-step / 540-second Muse limits and independent
Terra-high grading. The earlier interrupted run remains diagnostic only: four Muse
calls completed before Promptfoo's deferred grader queue was interrupted during call
five.

| Candidate | Train rows | Fully graded | Blocking recall | All-gold recall | Supported precision | False approvals | Verdict accuracy | Latency | Candidate tokens/cost | Novel human-verified |
| --- | ---: | ---: | --- | --- | --- | ---: | --- | --- | --- | ---: |
| current | 3 | 0 | unknown | unknown | unknown | unknown | unknown | unknown | unknown | 0 |
| minimal | 3 | 0 | unknown | unknown | unknown | unknown | unknown | unknown | unknown | 0 |
| risk-first | 3 | 0 | unknown | unknown | unknown | unknown | unknown | unknown | unknown | 0 |
| upstream-adapted | 3 | 0 | unknown | unknown | unknown | unknown | unknown | unknown | unknown | 0 |

No candidate may be promoted from this placeholder. A timeout is recorded as an
execution error, not as zero recall. Raw results, when authorized and available,
belong under the ignored `results/` directory and the table must be regenerated
from those rows.
