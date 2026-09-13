# Validation evaluation report

Revision rounds: 0/3

Status: locked until train identifies exactly two finalists. Set
`SPIKE_FINALISTS=spike-<first>,spike-<second>` only after the train report is
complete, then run `npm run eval:spike:validation`. It runs two frozen validation
controls for three repeats (twelve rows total; six per finalist).

| Candidate | Validation rows | Completed | Blocking recall | All-gold recall | Supported precision | False approvals | Verdict accuracy | Latency | Candidate tokens/cost | Novel human-verified |
| --- | ---: | ---: | --- | --- | --- | ---: | --- | --- | --- | ---: |
| finalist 1 | 6 | not run | unknown | unknown | unknown | unknown | unknown | unknown | unknown | 0 |
| finalist 2 | 6 | not run | unknown | unknown | unknown | unknown | unknown | unknown | unknown | 0 |

The empty-diff controls primarily exercise completion, contract, and false-positive
behavior; they cannot establish defect recall.
