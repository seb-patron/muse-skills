# Train evaluation report

Revision rounds: 0/3

Status: infrastructure-blocked in this checkout. The bounded command
`npm run eval:spike:train` was attempted under local execution permission, but the
first Spark row did not emit a completion within roughly ten minutes and the host
process was interrupted; no quality row completed and no model tokens were observed.
The command still enforces 12 rows, one pass for each of the four hashed candidates,
with Muse/Spark, the existing 24-step / 540-second limits, and independent
Terra-high grading.

| Candidate | Train rows | Completed | Blocking recall | All-gold recall | Supported precision | False approvals | Verdict accuracy | Latency | Candidate tokens/cost | Novel human-verified |
| --- | ---: | ---: | --- | --- | --- | ---: | --- | --- | --- | ---: |
| current | 3 | 0 | unknown | unknown | unknown | unknown | unknown | unknown | unknown | 0 |
| minimal | 3 | 0 | unknown | unknown | unknown | unknown | unknown | unknown | unknown | 0 |
| risk-first | 3 | 0 | unknown | unknown | unknown | unknown | unknown | unknown | unknown | 0 |
| upstream-adapted | 3 | 0 | unknown | unknown | unknown | unknown | unknown | unknown | unknown | 0 |

No candidate may be promoted from this placeholder. A timeout is recorded as an
execution error, not as zero recall. Raw results, when authorized and available,
belong under the ignored `results/` directory and the table must be regenerated
from those rows.
