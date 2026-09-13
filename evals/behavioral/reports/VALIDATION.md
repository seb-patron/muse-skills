# Validation evaluation report

Revision rounds: 0/3

Status: ready. Train selected `spike-current` and `spike-minimal`: current had the
strongest defect recall/verdict results, while minimal won the equal-quality
challenger tie on latency. Run
`SPIKE_FINALISTS=spike-current,spike-minimal npm run eval:spike:validation` once.
It executes two frozen validation controls for three repeats (twelve rows total;
six per finalist).

| Candidate | Validation rows | Completed | Blocking recall | All-gold recall | Supported precision | False approvals | Verdict accuracy | Latency | Candidate tokens/cost | Novel human-verified |
| --- | ---: | ---: | --- | --- | --- | ---: | --- | --- | --- | ---: |
| current | 6 | not run | unknown | unknown | unknown | unknown | unknown | unknown | unknown | 0 |
| minimal | 6 | not run | unknown | unknown | unknown | unknown | unknown | unknown | unknown | 0 |

The empty-diff controls primarily exercise completion, contract, and false-positive
behavior; they cannot establish defect recall.
