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

The Python provider creates a local, disposable repository containing only the
selected head and its ancestor history, checks that the base is an ancestor of the
head, disables web tools and network access, excludes
foreign personal context, and runs `muse exec --json`. Project scope takes
precedence over user scope in Muse, so the placeholder prevents a user-installed
copy of the tested skill from contaminating the control without changing the
developer's Muse configuration. The treatment replaces that placeholder with
the current skill body. The historical checkout contains no later source refs,
tags, objects, remotes, or alternates, so later evaluation files are not
recoverable through its Git database. This Git boundary is distinct from broader
host and tool isolation. Raw temporary
workspaces are deleted after each row.

The first two cases replay PR #1:

- `e2e3f5e` is the broken Promptfoo implementation. Its gold findings come from
  the later reproduced review: no agent run, shadowable probe rows, deletable
  load-bearing rules, a hard-coded layout count, and no CI enforcement.
- `33ebbdd` fixes those five findings and explicitly scopes itself as a structural
  checker. Cross-model calibration later exposed and reproduced a separate gap:
  arbitrary text after a valid bold probe header is ignored, so the case now has
  that sixth finding as human gold rather than remaining mislabeled as clean.

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
npm run eval:spike:validate
npm run eval:spike:probe        # one frozen candidate/case plus grading
npm run eval:spike:train        # four candidates across the three train cases
```

The ordinary behavioral provider accepts `MUSE_EVAL_MODEL` for baseline runs. The
evidence-backed spike ignores divergent overrides and requires its configured
`muse-spark-1.3-contributor` model to appear in runtime telemetry. The provider
records model ids surfaced by Muse, exact base/head SHAs, duration, event count,
and whether Muse emitted a successful `read_skill` result for the requested project
skill.
The skill condition explicitly requests that tool call and fails its
deterministic assertion if activation is not observed. Promptfoo telemetry and
update checks are disabled by the launcher. Raw results are written to the ignored
`evals/behavioral/results/` directory.

The spike pins target-provider concurrency to one and sets a 900-second eval-step
timeout. In Promptfoo 0.123, the presence of that timeout disables provider-grouped
deferred grading, so each slow Muse result reaches its Terra rubrics before the next
Muse row starts. The timer bounds the complete Muse-plus-Terra row; its 900-second
budget exceeds the Muse provider's 540-second process limit. Promptfoo may evaluate
the three rubrics for the row concurrently. `eval:spike:probe` bounds the wiring
check to one candidate/case row.

Promptfoo exits with status 100 when a completed row fails a quality assertion. The
spike runner accepts that one status long enough to verify exact row cardinality and
write normalized metrics; other nonzero statuses remain infrastructure failures.
The converter distinguishes a fully graded quality failure from a provider error,
timeout, missing rubric, or malformed review contract.

The smoke run is for wiring and manual inspection. Treat the three-repeat run,
not a single lucky completion, as the first comparison baseline.

## Cross-model references

The companion `cross-model-promptfooconfig.yaml` runs the same frozen cases and
grading contract through two Codex reference agents:

- [`gpt-5.6-luna`](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
  at medium reasoning is the lower-cost reference.
- [`gpt-5.6-sol`](https://developers.openai.com/api/docs/models/gpt-5.6-sol)
  at high reasoning is the quality-ceiling reference.

Each model has a control that explicitly withholds repository skills and a
treatment that injects the exact current `SKILL.md` into the prompt. The custom
provider uses an ephemeral Codex run in its own bounded-history repository.
Workspace writes are enabled there so mutation probes can run, while network
and web search are disabled; the source checkout is never writable. The run
also ignores user configuration and repository rules and records model, effort,
SHA, latency, event count, and token usage. No API key or credential is
committed; the run uses the operator's existing Codex login.

```sh
npm run eval:cross-model:validate
npm run eval:cross-model:smoke   # one pass per case/provider
npm run eval:cross-model         # three passes per case/provider
```

Cross-model runs are on demand because agentic repository reviews can be slow
and token-intensive. Start with the smoke run and inspect its raw output before
paying for three repeats.

These rows deliberately keep the human gold and Terra grader fixed. Compare
Luna with and without the skill, and Sol with and without the skill, to look for
transfer from the instructions. Compare the Codex rows with the Muse baseline
only as a cross-runtime reference: the agent harness, model family, tool
behavior, and skill-delivery mechanism all change together. The matrix cannot,
by itself, prove that a miss is caused only by Muse Spark's weights or only by
the prompt. See [`CROSS_MODEL_BASELINE.md`](CROSS_MODEL_BASELINE.md) for the
recorded preliminary run.

## Evidence-backed spike

The separate four-candidate experiment is documented in [`SPIKE.md`](SPIKE.md).
It preserves the current skill, adds minimal/risk-first/upstream-adapted prompt
candidates under `candidates/`, verifies their SHA-256 identities before delivery,
and keeps the eight-case corpus and frozen train/validation/held-out assignments in
`cases/spike_cases.yaml`. Validate its configuration without model calls:

```sh
uv run --with PyYAML==6.0.3 python evals/behavioral/experiment.py validate
npm run eval:spike:validate
```

The live sequence is bounded: one train run per candidate, three validation repeats
for exactly two finalists, then one held-out run only after human gold is
adjudicated and one finalist ID/hash is frozen. Every spike stage runs the structural
preflight, filters by `metadata.split`, checks its exact row/provider count, and
writes a deterministic `.metrics-v2.json` normalization beside the raw Promptfoo
output. `spike-heldout` is locked by default. The independent grader remains
`openai:codex-sdk:gpt-5.6-terra` at high reasoning; the tested Muse model is
`muse-spark-1.3-contributor`. The spike does not change the promoted skill or the
historical baseline configs.

Future normalization follows [`SCORING_V2.md`](SCORING_V2.md). Clean or unknown
gold denominators are excluded from recall, and aggregates expose applicability and
scored-row coverage. Committed v1 reports remain unchanged because their ignored raw
outputs are unavailable for an evidence-preserving recomputation.

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

Both agent providers select the candidate skill from each case's `skill_name`,
so they can run `review-loop` or `fix-verification` without a provider rewrite.
Muse installs it in the disposable project; Codex injects the same body into its
treatment prompt. The prompt and scoring must still match the behavior under
test:

- `adversarial-review`: defect recall, false approvals, evidence, and unrun probes.
- `fix-verification`: row custody, final-head repros, carried findings, and counters.
- `review-loop`: routing, role separation, round caps, stop conditions, and handoff.

After this harness lands, PR #2 should rebase on `main` and add its own cases.
In particular, reconcile its proposed `N/5` supervisor counter with the current
`fix-verification` `N/3` hard stop before treating either value as a gold rule.
