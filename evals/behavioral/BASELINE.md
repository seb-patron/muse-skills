# Behavioral baseline

This is a preliminary, one-repeat wiring baseline from 2026-09-12. It is not a
statistical comparison and should not be used to claim that one prompt is
better. The three-repeat command is the minimum comparison run for prompt
changes.

## Run identity

- Eval id: `eval-Vpx-2026-09-12T18:53:09`
- Candidate skill source: repository head `40d41039c22e8f33b451a34a514b27aa6ef7bc31`
- Muse: `1.1.1-R2514.1`, surfaced model `muse-spark-1.3-contributor`
- Promptfoo: `0.123.0`; Node.js: `26.7.0`
- Grader: `openai:codex-sdk:gpt-5.6-terra`, high reasoning
- Repeat count: 1; concurrency: 1; cache: disabled
- Total duration: 13m 5s; grader tokens reported by Promptfoo: 157,211

## Results

| Case | Condition | Verdict | Findings | Gold recall | Precision | Skill read | Muse latency |
| --- | --- | --- | ---: | ---: | ---: | --- | ---: |
| Broken PR #1 (`e2e3f5e`) | Placeholder control | APPROVE | 0 | 0.00 | 1.00 | no | 137s |
| Broken PR #1 (`e2e3f5e`) | Current skill | APPROVE | 0 | 0.00 | 1.00 | yes | 103s |
| Repaired PR #1 (`33ebbdd`) | Placeholder control | APPROVE | 0 | 1.00 | 1.00 | no | 197s |
| Repaired PR #1 (`33ebbdd`) | Current skill | APPROVE | 0 | 1.00 | 1.00 | yes | 298s |

Both conditions passed the repaired control and failed the defect-bearing head.
The current skill was definitely loaded, but it found none of the five verified
defects and returned the same false approval as the placeholder control. That is
the useful starting result: on this case, activation is working but the skill's
review procedure is not translating into recall.

The perfect precision scores are not evidence of strong reviews: every output
had zero findings, so there were no unsupported findings to penalize. Verdict
accuracy and defect recall keep the broken rows red. `npm run
eval:behavioral:smoke` therefore exits nonzero at this baseline by design.

## Calibration notes

The deterministic assertions agreed with manual inspection: all four responses
matched the JSON contract, used the correct head SHA, and had internally valid
evidence structure; the placeholder was not read and the current skill was read.
The model grader also agreed with the human gold on the central fact that neither
broken-head review detected any required finding.

During harness calibration an earlier review claimed that `actions/checkout@v7`
and `actions/setup-python@v7` did not exist. Current official release pages show
that they do, so the precision rubric now rejects unsupported external-release
claims when the offline reviewer has no current authoritative source. That
calibration run is excluded from the table above.

Raw outputs are intentionally ignored because they can be large and may contain
machine-specific paths. Re-run the suite to inspect them locally at
`evals/behavioral/results/latest.json`.

## Post-baseline gold correction

The Luna cross-model reference later found, and a human-local reproduction
confirmed, that `33ebbdd` accepts arbitrary text after the closing `**` of an
otherwise valid probe header. The existing regression test covered trailing
text inside the bold span only. The case is therefore no longer labeled as a
clean control: its expected verdict and human gold now include this separate
header-parser gap. The table above remains the exact historical score under the
original label; a future Muse run will grade against the corrected case.
