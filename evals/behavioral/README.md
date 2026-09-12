# Behavioral Muse review evaluations

This Promptfoo harness measures whether a Muse review skill changes review
behavior. It complements `evals/check_skills.py`, which remains the cheap
structural lint. A green structural check is not a behavioral result.

## Why Promptfoo first

Promptfoo already models the experiment this repository needs: run the same
case across providers, keep fixtures in reviewable YAML, mix deterministic and
model-graded assertions, repeat nondeterministic cases, and inspect failures.
Its [agent-skill guide](https://www.promptfoo.dev/docs/guides/test-agent-skills/)
also recommends an explicit with-skill/without-skill comparison. A small
[Python provider](https://www.promptfoo.dev/docs/providers/python/) is enough to
wrap the existing Muse CLI, so the first framework PR can stay focused on cases
and grading rather than a larger Python task/solver runtime.

Inspect AI remains a reasonable later choice if these evals grow into complex
multi-turn environments or reusable Python-native solvers. The cases and human
gold in this harness are intentionally plain data so they can migrate without
making Promptfoo the source of truth.

## What runs

Each Promptfoo row runs the same task twice:

1. `muse-control-placeholder` checks out the historical review head with an
   instruction-free project placeholder under the candidate skill's id.
2. `muse-current-adversarial-review` checks out the same head and stages the
   current `skills/<skill_name>/SKILL.md` under `.agents/skills/`.

The Python provider creates a local, disposable clone, checks that the base is
an ancestor of the head, disables web tools and network access, excludes
foreign personal context, and runs `muse exec --json`. Project scope takes
precedence over user scope in Muse, so the placeholder prevents a user-installed
copy of the tested skill from contaminating the control without changing the
developer's Muse configuration. The treatment replaces that placeholder with
the current skill body. The historical heads in the first suite predate these
labels, so the agent cannot read its answers from the fixture. Raw temporary
workspaces are deleted after each row.

The first two cases replay PR #1:

- `e2e3f5e` is the broken Promptfoo implementation. Its gold findings come from
  the later reproduced review: no agent run, shadowable probe rows, deletable
  load-bearing rules, a hard-coded layout count, and no CI enforcement.
- `33ebbdd` is the repaired structural-lint head. It is a paired control: those
  findings were fixed, and the lack of model calls is explicitly documented as
  the boundary of a structural checker rather than misrepresented as an eval.

## Who grades

Muse is always the system under test and never grades itself. Four Python
assertions deterministically check the JSON review contract, expected verdict,
skill-use trace evidence, and per-finding command evidence. Two model-graded
assertions use `openai:codex-sdk:gpt-5.6-terra` at high reasoning to score gold
finding recall and supported precision. The grader uses an existing Codex /
ChatGPT login when no API key is configured. Override it for a comparison with
Promptfoo's `--grader` option; do not change the tested Muse model in the same
experiment.

The human-authored gold set remains authoritative. A model-grader score is a
repeatable approximation that must be calibrated against reviewed outputs; it
does not promote a Codex or Claude review into truth automatically.

## Run it

Requirements:

- Node.js 22.22 or newer
- Python 3.12 or newer
- Muse Code installed and logged in
- Codex CLI logged in, or `OPENAI_API_KEY` / `CODEX_API_KEY` for the grader

```sh
npm install
npm run eval:behavioral:validate
npm run eval:behavioral:smoke   # one pass per case/provider
npm run eval:behavioral         # three passes per case/provider
```

Set `MUSE_EVAL_MODEL` to pin the tested Muse model. The provider records model
ids surfaced by Muse, exact base/head SHAs, duration, event count, and whether
Muse emitted a successful `read_skill` result for the requested project skill.
The skill condition explicitly requests that tool call and fails its
deterministic assertion if activation is not observed. Promptfoo telemetry and
update checks are disabled by the launcher. Raw results are written to the ignored
`evals/behavioral/results/` directory.

The smoke run is for wiring and manual inspection. Treat the three-repeat run,
not a single lucky completion, as the first comparison baseline.

## Add a case

Add a row to `cases/review_cases.yaml` with immutable base/head SHAs, an author
summary, an expected verdict, verified gold findings, and resolved findings for
clean controls. Gold findings need a concrete failure scenario and should be
reproduced by a human before inclusion. Never expose GitHub review comments or
gold files inside the agent workspace.

Use reviews from Muse, Codex, Claude, and humans as candidate annotations, then
deduplicate and verify them. If a review is used as a few-shot prompt example,
its PR belongs in the tuning set and must not remain in the held-out test set.

## Other skills and PR #2

The provider selects the installed project skill from each case's `skill_name`,
so it can run `review-loop` or `fix-verification` without a provider rewrite.
The prompt and scoring must still match the behavior under test:

- `adversarial-review`: defect recall, false approvals, evidence, and unrun probes.
- `fix-verification`: row custody, final-head repros, carried findings, and counters.
- `review-loop`: routing, role separation, round caps, stop conditions, and handoff.

After this harness lands, PR #2 should rebase on `main` and add its own cases.
In particular, reconcile its proposed `N/5` supervisor counter with the current
`fix-verification` `N/3` hard stop before treating either value as a gold rule.
