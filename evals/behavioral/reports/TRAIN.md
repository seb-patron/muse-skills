# Train evaluation report

Revision rounds: 0/3

Status: infrastructure repair awaiting a fresh one-row probe. In the preserved
interrupted train, the first four Muse/Spark candidate calls completed in 140-203
seconds with exit code 0, the pinned model, and observed skill activation. Promptfoo
0.123 deferred their Terra rubrics until all 12 candidate calls, and the host process
was interrupted during candidate call five. The queued graders were then aborted
before start, so no fully graded quality row completed. Candidate usage telemetry was
absent rather than measured as zero. The repaired command still enforces 12 rows, one
pass for each of the four hashed candidates, with the existing 24-step / 540-second
Muse limits and independent Terra-high grading.

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
