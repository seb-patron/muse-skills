# muse-skills

Review-loop skills for Muse sessions, built from a real incident: two round-1
reviewers approved with 0 blocking findings while an independent review found
13 real issues. These skills encode the protocol that caught everything in
rounds 2–3.

| Skill | What it does |
|---|---|
| [skills/adversarial-review](skills/adversarial-review/SKILL.md) | Pre-verdict adversarial probe pass over a green-gated diff: a runnable catalogue of probes (imports, worktrees, guard evasions, vendoring, builds, help text, docs-as-written) plus a refutation pass before APPROVE. |
| [skills/fix-verification](skills/fix-verification/SKILL.md) | Post-fix closeout: one traceability row per finding, diff-to-row mapping, per-row repro re-runs on the final head (incl. after rebases), status-field consistency, and a `Revision rounds: N/3` counter. |
| [skills/review-loop](skills/review-loop/SKILL.md) | Supervisor layer: attach plan refutation (Round 0) plus bounded implementer → reviewer → fixer rounds (`Revision rounds: N/5`, hard cap), role separation, artifact conventions, push discipline, dead-agent handling, and human-handoff stop conditions. |

Background (evidence, not required reading): the miss analysis, skills review,
and cross-review that produced these live in the originating work log, and each
skill names the concrete misses its steps catch.

## Use in a Muse session

Skills install from a local path. Clone once, install each skill (user scope
shown; omit `--scope user` for project scope):

```sh
git clone https://github.com/seb-patron/muse-skills.git
muse skills install muse-skills/skills/adversarial-review --scope user
muse skills install muse-skills/skills/fix-verification --scope user
```

Then invoke by name (`adversarial-review`, `fix-verification`) or let the
session load them when a review/fix round starts.

## Structural validation

The lint validates tracked skill roots, YAML metadata, the 14 probe identities,
and selected instruction fragments. It uses a pinned PyYAML dependency; CI runs
the lint and its regression tests on every PR. From the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r evals/requirements.txt
.venv/bin/python evals/check_skills.py
.venv/bin/python -m unittest discover -s evals -p 'test_*.py' -v
```

On Windows, use `.venv\Scripts\python.exe` instead of `.venv/bin/python`.
This checks document structure and text presence; it does not execute Muse or
establish that Muse loads or follows the skills correctly. See
[evals/README.md](evals/README.md) for the contract and its limitations.

## Behavioral evaluation

The Promptfoo harness under [evals/behavioral](evals/behavioral/README.md) runs
frozen review tasks through Muse twice: an instruction-free placeholder control
and the current skill. It grades the machine-readable review contract
deterministically and uses an independent Codex model to compare the review
against human-verified findings. The first cases replay the broken and later
structural-lint stages from PR #1, including a subsequently discovered parser
gap.

```sh
npm install
npm run eval:behavioral:validate
npm run eval:behavioral:smoke
```

Use `npm run eval:behavioral` for the three-repeat comparison baseline. These
runs make model calls and are kept on demand rather than in the per-PR lint job.

For a cross-runtime reference, the same frozen cases can also run through Codex
Luna and Sol, both without the candidate skill and with its exact current body
injected into the prompt:

```sh
npm run eval:cross-model:validate
npm run eval:cross-model:smoke
```

See the [cross-model baseline](evals/behavioral/CROSS_MODEL_BASELINE.md) before
interpreting that comparison. It is a reference for task solvability and skill
transfer, not a controlled swap of the model running inside Muse.

The evidence-backed retain/replace/retire spike is documented in
[evals/behavioral/SPIKE.md](evals/behavioral/SPIKE.md). It is experimental and
does not alter the promoted `adversarial-review` skill.

The disclosed development calibration has immutable v2 evidence and an additive
[corrected v3 profile](evals/behavioral/DEVELOPMENT_V3.md). Offline validation is
available as `npm run eval:development-v3:validate`; no v3 live run is implied.

## Contributing

`main` is protected: all changes land via pull request with one approval
(stale reviews dismissed on push). Keep each skill's root document at
`skills/<skill-id>/SKILL.md`; nested supporting files are allowed. Stage new
skill files so the structural lint discovers them, run the gates above, and
validate the skill with Muse before opening a PR:

```sh
muse skills validate skills/<skill-id> --json
```

Both skills shipped `valid: true` with zero diagnostics.
