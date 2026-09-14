# Exact-boundary and evidence-claim development experiment v2

Status: offline-ready; no live inference or grading has run.

Protocol: `muse-adversarial-review-development-v2`.

Normalization: `muse-review-metrics-v2`.
Data role: disclosed development only.

This experiment asks whether one targeted candidate improves Muse/Spark review
behavior over the exact current `adversarial-review` skill on two independently
reproduced failures without rejecting a repaired, nonempty change. It is separate
from the frozen four-candidate v1 spike. It does not change v1 candidates, cases,
splits, finalist selection, reports, or the held-out lock, and it does not modify
the production skill.

## Candidate and fixed profile

The immutable candidate manifest is
[`candidates/development-v2-manifest.yaml`](candidates/development-v2-manifest.yaml).
It compares only:

1. `current`, the exact promoted skill at SHA-256
   `dd4f217a140beb155f209f9215932fa44e84936096796ade725f49199c239380`;
2. `evidence-claims-v2`, a 98-line candidate at SHA-256
   `feea0437ae4f5218a6795d11fd2f31404cc445308159f8946c38b897d87629f6`.

The candidate adds two bounded mechanisms: recursively attack one exact structured
boundary, and independently derive up to five merge-critical claims from their
source artifacts before spending a maximum of three focused probes. The existing
output contract and finalization requirement remain fixed.

Both treatments use `muse-spark-1.3-contributor`, high reasoning, 24 model steps,
a 540-second Muse process timeout, restricted network, disabled web tools, and the
same project-skill activation path. Terra at high reasoning remains the independent
semantic grader. Candidate gold is never included in the review prompt.

## Three-case disclosed family pack

The authoritative pack is
[`cases/development-v2-cases.yaml`](cases/development-v2-cases.yaml). Every row is
development data disclosed before candidate freeze. These rows cannot support a
held-out, validation, or generalization claim.

| Case | Exact head | Bounded oracle |
| --- | --- | --- |
| PR #87 first repair | `8850ee389109d22bd2ce7b4d3a23ae50ff3c1ddc` | `NEEDS_FIXES`: ordinary nested equality accepts and emits equal-valued numeric type aliases. |
| PR #90 evidence head | `3ecd94150e5030b88b1111d17706d6f4c0063ed4` | `NEEDS_FIXES`: the note's every-adjacent-pair claim contradicts its fixture. |
| PR #90 synchronized head | `e45d8eb3be5edfd2315f021119063401d5beb794` | `APPROVE` within the documented changed-artifact and focused-gate scope. |

PR #90's broken and repaired heads share `family: genv-pr90`; they must remain in
the same data role. The final control is a real nonempty base-to-head change, not an
empty-diff wiring row.

The labels are independently reproduced development oracles, not human-adjudicated
gold. At PR #87's frozen head, a synthetic summary with integer `execution.frames`
changed to equal-valued float `3600.0` passed `assemble()` and the emitted fixture
retained `float 3600.0`. At PR #90's evidence head, independent fixture arithmetic
found 11 changing intervals among 13 for both repetitions of all four controls;
`[2299,2300]` and `[2303,2304]` were stable in every attempt, while the tracked note
claimed every adjacent pair changed. Neither reproduction used commercial input or
an emulator.

The PR #90 final head passed 74 focused tests, the privacy gate, tracked-input
integrity, and diff whitespace checks in a clean detached checkout. Its note derives
11/13 and names both stable pairs. The full 728-test suite and hosted checks were not
rerun during intake, so this record does not claim broader clean status or human
adjudication. Supported novel findings from a future row still require adjudication.

## Authenticated private-source boundary

Gen V Research Tools is private. The native Muse provider recognizes its fixed source
ID and uses the operator's existing Git authentication plus network access to prepare
the source before Muse starts. A future authorized live run therefore sends a private
exact-head checkout to Muse. The Muse process itself receives restricted network and
disabled web tools.

Source preparation fetches only the requested exact head and ancestor history,
verifies the pinned base/head tree identities and ancestry, checks out the head, and
retains no remote or fetch record. Later commits and review comments are absent from
the candidate workspace. The project skill is delivered from this Muse repository.
Committed Muse artifacts vendor no Gen V source or private output. They retain only
mechanisms and defect descriptions already disclosed in public Muse issue #5 plus
sanitized commit and tree identities and reproduction summaries.

This establishes the tested Git-history visibility boundary. It is not a claim of
complete host, process, credential, cache, or tool isolation.

## Offline validation and future run card

With Python, PyYAML, Node, and the pinned package dependencies already installed,
offline validation performs no model call, private-source fetch, or network action:

```sh
SPIKE_PYTHON=/path/to/python-with-pyyaml npm run eval:development-v2:validate
```

Dependency installation is separate setup and may require package-registry network
access. `uv run --with PyYAML==6.0.3 ...` is convenient setup plus validation, but is
not fully offline unless that dependency is already cached.

The future comparison requests exactly six candidate executions: two candidates times
three cases, one logical attempt each, target-provider concurrency one, and up to 24
Muse model steps per execution. Three Terra rubrics per row create a nominal 18
logical grader evaluations. That is not a claim of exactly 18 underlying model API
calls: Promptfoo/provider retry behavior and grader call implementation are not pinned
by this run card. It is a directional development screen, not a repeat-backed
selection or tournament.

```sh
npm run eval:development-v2
```

That command has not been run and requires separate live-inference authorization.
The authorization must include authenticated private-source preparation and sending
the bounded checkout to Muse. The command rejects extra Promptfoo arguments and
refuses to start if the raw output, v2 metrics, exclusive reservation, or dedicated
Promptfoo cache already exists. Before any output, cache creation, or child process,
it sets a private creation mask; the reservation and newly created raw/normalized
files are owner-only, and the dedicated cache directory is owner-only. It creates
the reservation atomically before inference, then writes `development-v2.json` and
`development-v2.json.metrics-v2.json`.

Keep the reservation, dedicated Promptfoo cache, and every raw, partial, error, and
normalized artifact together as private material.
If a run stops before all rows or grading complete, do not delete or overwrite it;
archive the complete attempt set before requesting and starting any later rerun. The
runner does not claim zero retries, and this run card sets no enforceable token or
money cap. Its enforceable bounds are the declared rows/concurrency, per-execution
step/tool-output/process limits, and per-row timeout. Completed quality failures
remain evidence; timeouts and provider/grader failures remain errors. Candidate token
usage is summed only when provider telemetry reports it. Missing usage is
`unavailable`, never zero.

Before using results to change the skill, inspect all six rows, adjudicate novel or
disputed findings, reproduce any proposed new gold, add genuinely separate unseen
evidence if a broader claim is needed, and promote a winner only in a separate PR.
