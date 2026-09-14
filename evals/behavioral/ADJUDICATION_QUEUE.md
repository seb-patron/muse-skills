# Human adjudication queue

Revision rounds: 0/3

AI reviews and historical PR descriptions are candidate annotations only. They are
not human gold. The three pending historical cases below are frozen as held-out
until Sebastian reproduces and adjudicates any findings. Do not run prompt tuning
against them; once their gold is disclosed, replace them before using them as a
held-out test.

| Case | Base → head | Why it is queued | Human action needed |
| --- | --- | --- | --- |
| `pr2-review-loop` | `c18a44d` → `56c3d13` | Adds the supervisor skill and a Round 0/round-cap protocol. | Review the introduced behavior and docs-as-written contract; reproduce any concrete failure and classify severity. |
| `pr4-cross-model-reference` | `adbf38a` → `aa2be041` | Adds Codex reference providers and a preliminary cross-model matrix. | Verify provider isolation, model/budget claims, and whether recorded conclusions are supported by the committed harness. |
| `pr3-behavioral-baseline` | `40d4103` → `d04c043` | Adds the initial Promptfoo behavioral harness. | Reproduce configuration/provider behavior and adjudicate any unsupported evaluation or isolation claims. |

For each candidate finding, record the exact file and line, a minimal local
reproduction, observed output, impact, and the adjudicator's decision. A model
grader may help triage wording but cannot fill this queue.
