# Development calibration v3

Status: offline-ready; no v3 candidate execution or grading has run.

Protocol: `muse-adversarial-review-development-v3`

Normalization: unchanged `muse-review-metrics-v2`

Data role: disclosed development only

Development v3 is a versioned correction to the three-case development screen.
The executed v2 manifest, case pack, Promptfoo config, raw results, normalized
metrics, scores, and grader reasons remain immutable historical evidence and are
not rewritten.
The two candidate files and their hashes are also unchanged.

The correction makes two calibration boundaries explicit:

1. The nonempty approval control has one independently reproduced low
   review-history metadata mismatch. It still expects `APPROVE` because the issue
   does not invalidate the behavior, evidence, or focused gates. The v2 statement
   that the relevant finalization metadata was fully synchronized was false.
2. Gold-defect recall asks whether a review detected the annotated defect and its
   impact. The gold annotation determines whether that defect belongs in the
   blocking denominator. A candidate's different severity label is disclosed in
   the grader reason, but does not silently turn an applicable gold defect into a
   miss or make it inapplicable. This protocol does not add a severity-agreement
   metric or claim that severity agreement was established.

The corrected artifacts are
[`candidates/development-v3-manifest.yaml`](candidates/development-v3-manifest.yaml),
[`cases/development-v3-cases.yaml`](cases/development-v3-cases.yaml), and
[`development-v3-promptfooconfig.yaml`](development-v3-promptfooconfig.yaml).
They retain the v2 source SHAs, candidate hashes, Muse/Spark runtime, Terra grader,
budgets, private-source boundary, exact row count, and `muse-review-metrics-v2`
normalizer. Gold remains grader-only.

Offline validation uses the prepared Python environment and pinned Node
dependencies and makes no model call:

```sh
SPIKE_PYTHON=/path/to/python-with-pyyaml npm run eval:development-v3:validate
```

`npm run eval:development-v3` is a separate future live run. It requests the same
six candidate executions and nominal 18 logical rubric evaluations as v2, writes
to a distinct `development-v3.json` result identity, and retains the existing
exclusive reservation, private cache, no-overwrite, row-completion, and error
handling. Missing candidate usage remains unavailable. Any live run still requires
specific authorization for inference and private-source delivery.

Offline tests prove the pinned configuration and deterministic normalizer wiring.
Synthetic rows cover a blocking gold defect that was found under a different
candidate severity and the same defect being missed. They do not validate how a
future Terra call will apply the revised rubric. A live calibration run and review
of grader reasons remain necessary before comparing v3 scores with the historical
v2 run. Do not directly combine or compare v2 and v3 aggregate recall: v3 adds a
low finding to the approval-control denominator, while v2 correctly excluded its
then-`NONE` denominator under the frozen case contract. Neither version supports a
winner, skill-promotion, held-out, or full issue #5 completion claim.
