# evidence-claims-v3 screen

Status: wired and checked offline. No Muse or grader call has been made for this
screen. A live run needs owner authorization recorded on issue #18.

Tracking issue: [#18](https://github.com/seb-patron/muse-skills/issues/18)

This screen answers one question: does the unchanged `evidence-claims-v3`
candidate from PR #16 catch the missed evidence claim, keep the type-boundary
finding, and still approve the repaired control? It compares that candidate with
`evidence-claims-v2`.

`evidence-claims-v3` is a skill candidate. `development-v3` is the evaluation
profile whose cases, prompt, grader, rubrics and budgets this screen reuses. The
earlier development-v3 calibration did not run v3.

## What is reused and what is new

| Reused unchanged | New for this screen |
| --- | --- |
| `cases/development-v3-cases.yaml` (3 disclosed development cases) | `candidates/evidence-claims-v3-screen-manifest.yaml` |
| `prompts/review.txt`, the three Terra rubrics, six Python assertions | `evidence-claims-v3-screen-promptfooconfig.yaml` |
| Muse runtime, 24 model steps, 540 s process limit, 200 KB tool output | `evidence-claims-v3` entry in the Muse provider allowlist |
| Isolated exact-head Git checkouts, private result custody, no-overwrite runner | `evidence-claims-v3-screen` runner stage and validator |

The config differs from `development-v3-promptfooconfig.yaml` only in its
description and its two provider entries. The validator pins both candidate
hashes, rejects any other runtime, budget, grader, rubric, prompt or case pack,
and reuses the hash-pinned development-v3 case validation.

| Candidate | Path | SHA-256 |
| --- | --- | --- |
| `evidence-claims-v2` | `candidates/evidence-claims-v2.md` | `feea0437ae4f5218a6795d11fd2f31404cc445308159f8946c38b897d87629f6` |
| `evidence-claims-v3` | `candidates/evidence-claims-v3.md` | `d357350e83f01429f97aa5903404ffe9342677bb16e39242024878958dfc4477` |

## Answer-key boundary

The review prompt carries no gold or resolved findings; those are available only
to the rubric grader. Each Muse review runs in a disposable checkout that
contains only the case's head ancestry. That checkout sits under this
repository because Muse requires it, so the provider now fails a row as an
execution error if the Muse trace contains a grader-only field name, a case-pack
or diagnosis-report filename, or an eval-repository path outside the disposable
checkout. That is a trace check, not a sandbox: a read that never shows up in
the trace (for example a relative path with none of the checked names) would not
be caught.

## Commands

Offline, with no model call:

```sh
SPIKE_PYTHON=/path/to/python-with-pyyaml npm run eval:evidence-claims-v3-screen:validate
```

Live, only after authorization:

```sh
SPIKE_PYTHON=/path/to/python-with-pyyaml npm run eval:evidence-claims-v3-screen
```

The live stage runs 2 candidates × 3 cases × 1 fresh execution = 6 Muse
reviews with `--no-cache`, then at most 3 Terra rubric evaluations per completed
row (at most 18). It writes privately to
`results/evidence-claims-v3-screen.json`, refuses to overwrite any earlier or
partial attempt, and does not retry. A failed row stays a failed row.
