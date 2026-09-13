# Validation evaluation report

Revision rounds: 0/3

Status: complete. Frozen validation `eval-pNM-2026-09-13T02:31:20` produced exactly
12 fully graded rows with zero assertion failures or execution errors in 9m26s.
Promptfoo recorded 697,277 Terra grading tokens across 36 rubric requests.
Candidate token/cost telemetry remained unavailable and is not reported as zero.
Terra used 349,344 tokens for current's 18 rubric requests and 347,933 for
minimal's 18 requests.

The run used both frozen clean empty-diff controls for three repeats per finalist:
six rows each for `spike-current` and `spike-minimal`. All 12 Muse calls exited 0,
observed explicit skill activation, and reported only the pinned
`muse-spark-1.3-contributor` model. Every output was `APPROVE` with zero findings,
matching clean-case gold `NONE`.

| Candidate | Validation rows | Completed | Blocking recall | All-gold recall | Supported precision | False approvals | Verdict accuracy | Latency | Candidate tokens/cost | Novel human-verified |
| --- | ---: | ---: | --- | --- | --- | ---: | --- | --- | --- | ---: |
| current | 6 | 6 | 1.000 | 1.000 | 1.000 | 0 | 1.000 | 31.7s | unavailable | 0 |
| minimal | 6 | 6 | 1.000 | 1.000 | 1.000 | 0 | 1.000 | 28.8s | unavailable | 0 |

The empty-diff controls primarily exercise completion, contract, and false-positive
behavior; their recall scores are vacuous and cannot establish defect recall.
Minimal was 2.9s (10.1%) faster on these controls, while current retained the
stronger train quality result. Validation therefore did not overturn the train
quality ordering. `spike-current` is frozen as the sole held-out finalist at SHA-256
`dd4f217a140beb155f209f9215932fa44e84936096796ade725f49199c239380`.
This is not a production retain/replace decision; held-out execution remains locked
pending human gold and any required replacement of disclosed cases.
