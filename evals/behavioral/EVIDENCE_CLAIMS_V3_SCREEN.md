# evidence-claims-v3 screen

Status: wired and checked offline. No Muse call has been made for this screen.
A live run needs owner authorization recorded on issue #18.

Tracking issue: [#18](https://github.com/seb-patron/muse-skills/issues/18)

This screen asks one question: does the unchanged `evidence-claims-v3`
candidate from PR #16 catch the missed evidence claim, keep the type-boundary
finding, and still approve the repaired control? It compares that candidate
with `evidence-claims-v2`.

`evidence-claims-v3` is a skill candidate. `development-v3` is the evaluation
profile. This screen reuses that profile's cases, prompt, deterministic checks
and budgets. The earlier development-v3 calibration did not run v3.

## Grading: review-only, deterministic baseline

The owner chose not to use an LLM grader for this screen, so it makes
**6 Muse calls and 0 grader calls**.

Each row gets the development-v3 deterministic checks (review contract,
verdict, skill observation, candidate identity, model telemetry, evidence
shape). It also gets an `answer_key_boundary` check, described under
"Answer-key boundary" below.

Finding quality is judged against
[`answer-keys/evidence-claims-v3-screen-v1.yaml`](answer-keys/evidence-claims-v3-screen-v1.yaml).
That file is frozen before execution, and its hash is pinned by the validator.
It changes one annotation relative to the historical case pack: the Case 1
finding is `should-fix`, not `blocking`. The case pack itself is unchanged.

After the run, a separate Claude pass reads the saved reviews against those
keys. It flags likely detections, misses, resolved issues reported as live,
and novel findings, so the owner only has to check disagreements. That pass is
advisory: it is recorded separately and never changes a deterministic result.
Terra rubric grading can be applied to the same retained reviews later under
separate authorization.

## What is reused and what is new

| Reused unchanged | New for this screen |
| --- | --- |
| `cases/development-v3-cases.yaml` (3 disclosed development cases) | `candidates/evidence-claims-v3-screen-manifest.yaml` |
| `prompts/review.txt` and the six deterministic assertions | `evidence-claims-v3-screen-promptfooconfig.yaml` (review-only) |
| Muse runtime, 24 model steps, 540 s process limit, 200 KB tool output | `answer-keys/evidence-claims-v3-screen-v1.yaml` |
| Exact-head Git checkouts, private result custody, no-overwrite runner | `evidence-claims-v3` provider entry, `answer_key_boundary` assertion, runner stage and validator |

| Candidate | Path | SHA-256 |
| --- | --- | --- |
| `evidence-claims-v2` | `candidates/evidence-claims-v2.md` | `feea0437ae4f5218a6795d11fd2f31404cc445308159f8946c38b897d87629f6` |
| `evidence-claims-v3` | `candidates/evidence-claims-v3.md` | `d357350e83f01429f97aa5903404ffe9342677bb16e39242024878958dfc4477` |

## Answer-key boundary: detected, not prevented

The review prompt carries no gold. For this stage the runner creates each
disposable checkout outside the repository, under
`~/.cache/muse-skill-eval/run-<time>-<pid>/`. Muse also gets an environment
without eval-harness variables or repository PATH entries.

**This is not isolation.** A probe run (two Muse calls on a throwaway repo, with
no private data) showed that Muse's shell sandbox restricts network access but
not filesystem reads. The reviewer ran `cat ../file`, `cat` on a file elsewhere
in the home directory, and `ls` of another project folder, and none of those
was blocked. Every earlier run in this project had the same exposure.

The owner accepted **detect-and-quarantine** for this six-review development
screen instead of verified prevention. Muse's own sandbox stays on.

The provider scans every attempt's event log, including timeouts and errors.
It flags:

- grader-only field names, answer-key, case-pack and diagnosis filenames;
- eval-repository paths, and paths beside the checkout;
- in every command Muse logged (from `tool.result` and task output events) or
  reported in its review:
  - absolute paths outside the checkout; the only exceptions are `/dev/null`
    and friends, and executables directly in `/bin` or `/usr/bin`;
  - `..` traversal;
  - `$HOME` or `~`;
  - directory changes other than into a plain relative path;
  - indirect access such as `git -C`, `--git-dir`, alternates, or environment
    and home lookups.

A flag, or a missing or failed trace, marks the row **QUARANTINE**.

Every row is audited before scores are interpreted. The audit separates three
things: a path that is only *mentioned*, an *attempted* access, and *returned*
content. A row with confirmed or unresolved exposure is excluded. A cleared
false positive keeps its saved review, and Muse is never rerun.

A clean scan means **no exposure was observed**, not that none occurred. For
example, a script that builds a path at runtime, or a Git alternate object
store, would not necessarily show a path in the command text.

Before a larger run (#19), the reviewer should run under a separate OS identity
that cannot read the eval repository, grading material, earlier traces, or
later clones of the case source. Actual read denials and network restrictions
must be verified first.

## Evidence retention

Each attempt's raw Muse stdout and stderr are written with mode 0600 to
`results/evidence-claims-v3-screen.json.traces/`, a mode-0700 directory
ignored by git. This includes partial output from a timeout. Row metadata
records:

- the trace file names and SHA-256 hashes;
- `durationMs` and `termination`;
- any token usage Muse emitted.

A row whose trace was not retained cannot be audited, so it is quarantined
even if its final review survived. All six attempts stay in the accounting;
excluded, quarantined and failed rows are not dropped. Only eligible v2/v3 pairs
are compared, and a case that loses either side of its pair is inconclusive.

## Commands

Offline, with no model call:

```sh
SPIKE_PYTHON=/path/to/python-with-pyyaml npm run eval:evidence-claims-v3-screen:validate
```

Live, only after authorization:

```sh
SPIKE_PYTHON=/path/to/python-with-pyyaml npm run eval:evidence-claims-v3-screen
```

The live stage runs 2 candidates × 3 cases × 1 fresh execution with
`--no-cache`, which is 6 Muse reviews. It refuses to overwrite any earlier or
partial attempt and does not retry. The runner exits non-zero if any row
errored. The saved rows and traces are kept in that case.
